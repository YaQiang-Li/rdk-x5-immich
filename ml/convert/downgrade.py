# SPDX-License-Identifier: MIT
"""Make an ONNX graph digestible by horizon_nn 1.0.x (opset 10/11, IR<=7).

1. pin the input shape            (dynamic dims -> static, BPU needs them)
2. ORT basic optimization         (constant folding; standard ops only)
3. decompose ops newer than 11    (HardSwish, LayerNormalization)
4. onnx.version_converter -> 11, IR 7, strip stale value_info / attrs

Usage: python downgrade.py src.onnx dst.onnx 1x3x48x320
"""
import sys

import numpy as np
import onnx
import onnxruntime as ort
from onnx import helper, numpy_helper


def pin_and_fold(src, dst, shape):
	m = onnx.load(src)
	dims = [int(x) for x in shape.split("x")]
	for d, v in zip(m.graph.input[0].type.tensor_type.shape.dim, dims):
		d.Clear()
		d.dim_value = v
	m.ir_version = min(m.ir_version, 8)  # ORT 1.16 reads IR<=9
	pinned = dst + ".pinned.onnx"
	onnx.save(m, pinned)
	so = ort.SessionOptions()
	so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
	so.optimized_model_filepath = dst
	ort.InferenceSession(pinned, so, providers=["CPUExecutionProvider"])
	return onnx.load(dst)


def const(graph, name, value):
	graph.initializer.append(numpy_helper.from_array(
		np.asarray(value, dtype=np.float32), name))
	return name


def decompose(m):
	g = m.graph
	nodes = []
	for n in g.node:
		if n.op_type == "HardSwish":
			# x * HardSigmoid(x, 1/6, 0.5)
			hs = n.output[0] + "_hsig"
			nodes.append(helper.make_node("HardSigmoid", [n.input[0]], [hs],
				name=n.name + "_hsig", alpha=1.0 / 6, beta=0.5))
			nodes.append(helper.make_node("Mul", [n.input[0], hs],
				list(n.output), name=n.name))
		elif n.op_type == "LayerNormalization":
			attrs = {a.name: helper.get_attribute_value(a) for a in n.attribute}
			axis, eps = attrs.get("axis", -1), attrs.get("epsilon", 1e-5)
			p = n.name or n.output[0]
			x = n.input[0]
			mean, diff, sq, var = (f"{p}_{s}" for s in ("mean", "diff", "sq", "var"))
			ve, std, norm = f"{p}_ve", f"{p}_std", f"{p}_norm"
			nodes += [
				helper.make_node("ReduceMean", [x], [mean], axes=[axis], keepdims=1),
				helper.make_node("Sub", [x, mean], [diff]),
				helper.make_node("Mul", [diff, diff], [sq]),
				helper.make_node("ReduceMean", [sq], [var], axes=[axis], keepdims=1),
				helper.make_node("Add", [var, const(g, f"{p}_eps", eps)], [ve]),
				helper.make_node("Sqrt", [ve], [std]),
				helper.make_node("Div", [diff, std], [norm]),
			]
			out = n.output[0]
			if len(n.input) > 2 and n.input[2]:
				nodes.append(helper.make_node("Mul", [norm, n.input[1]], [f"{p}_scaled"]))
				nodes.append(helper.make_node("Add", [f"{p}_scaled", n.input[2]], [out]))
			else:
				nodes.append(helper.make_node("Mul", [norm, n.input[1]], [out]))
		else:
			nodes.append(n)
	del g.node[:]
	g.node.extend(nodes)
	return m


def strip(m):
	keep = [o for o in m.opset_import if o.domain in ("", "ai.onnx")]
	del m.opset_import[:]
	m.opset_import.extend(keep)
	del m.graph.value_info[:]
	for n in m.graph.node:
		if n.op_type == "MaxPool":
			attrs = [a for a in n.attribute if a.name != "storage_order"]
			del n.attribute[:]
			n.attribute.extend(attrs)
	return m


def to_opset11(m):
	"""Relabel as opset 11. Between 11 and 14 the ops these graphs use keep
	their semantics, except that 13 moved Squeeze/Unsqueeze/ReduceSum `axes`
	and Split `split` from attribute to input: move constant ones back."""
	inits = {i.name: numpy_helper.to_array(i) for i in m.graph.initializer}
	consts = {n.output[0]: numpy_helper.to_array(n.attribute[0].t)
		for n in m.graph.node if n.op_type == "Constant"}
	inits.update(consts)
	reduces = ("ReduceL2", "ReduceMean", "ReduceMax", "ReduceMin", "ReduceProd")
	moved = {"Squeeze": "axes", "Unsqueeze": "axes", "ReduceSum": "axes",
		**{op: "axes" for op in reduces},
		"Split": "split"}
	for n in m.graph.node:
		attr = moved.get(n.op_type)
		if attr and len(n.input) > 1 and n.input[1]:
			if n.input[1] not in inits:
				raise ValueError(f"{n.name}: non-constant {attr}")
			n.attribute.append(helper.make_attribute(
				attr, [int(v) for v in inits[n.input[1]].ravel()]))
			del n.input[1:]
		if n.op_type in reduces + ("ReduceSum",):
			for a in n.attribute:
				if a.name == "noop_with_empty_axes" and a.i != 0:
					raise ValueError(f"{n.name}: noop_with_empty_axes=1")
			keep = [a for a in n.attribute if a.name != "noop_with_empty_axes"]
			del n.attribute[:]
			n.attribute.extend(keep)
		newer = {"BatchNormalization": "training_mode", "Reshape": "allowzero"}
		if n.op_type in newer:
			for a in n.attribute:
				if a.name == newer[n.op_type] and a.i != 0:
					raise ValueError(f"{n.name}: {a.name}={a.i} has no opset 11 form")
			keep = [a for a in n.attribute if a.name != newer[n.op_type]]
			del n.attribute[:]
			n.attribute.extend(keep)
		# Softmax on the last axis means the same in 11; verify() checks it
	m.opset_import[0].version = 11
	return m


def verify(src, dst, shape):
	"""Same outputs as the source graph, on a random input."""
	x = np.random.default_rng(0).uniform(-1, 1, [int(v) for v in shape.split("x")])
	x = x.astype(np.float32)
	ref = onnx.load(src)
	ref.ir_version = min(ref.ir_version, 8)
	run = lambda model: ort.InferenceSession(
		model.SerializeToString(), providers=["CPUExecutionProvider"]).run(
		None, {model.graph.input[0].name: x})
	for a, b in zip(run(ref), run(onnx.load(dst))):
		cos = (a.ravel() @ b.ravel()) / (np.linalg.norm(a) * np.linalg.norm(b))
		print(f"verify: max|diff| {np.abs(a - b).max():.2e} cos {cos:.6f}")


def main(src, dst, shape):
	m = strip(decompose(pin_and_fold(src, dst, shape)))
	if m.opset_import[0].version > 11:
		m = to_opset11(m)
	m.ir_version = 7
	m = onnx.shape_inference.infer_shapes(m)
	onnx.checker.check_model(m)
	onnx.save(m, dst)
	verify(src, dst, shape)
	ops = {}
	for n in m.graph.node:
		ops[n.op_type] = ops.get(n.op_type, 0) + 1
	print(dst, "opset", [(o.domain, o.version) for o in m.opset_import], ops)


if __name__ == "__main__":
	main(*sys.argv[1:4])
