# SPDX-License-Identifier: MIT
"""Replace PRelu(x, a) with Relu(x) + a * (x - Relu(x)).

Only Relu/Sub/Mul/Add, all BPU ops (Neg is not: it splits the graph).
hb_mapper 1.23/1.24 quantize PRelu into a fixed-point model far off the
calibrated one (ArcFace R50: cos 0.14); this form quantizes correctly.
"""
import sys

import numpy as np
import onnx
from onnx import helper, numpy_helper


def main(src, dst):
	m = onnx.load(src)
	g = m.graph
	inits = {i.name: i for i in g.initializer}
	nodes = []
	for n in g.node:
		if n.op_type != "PRelu":
			nodes.append(n)
			continue
		x, a = n.input
		slope = numpy_helper.to_array(inits[a]).astype(np.float32)
		if slope.ndim == 1:
			slope = slope.reshape(-1, 1, 1)
		alpha = numpy_helper.from_array(slope, a + "_alpha")
		g.initializer.append(alpha)
		p = n.name
		nodes += [
			helper.make_node("Relu", [x], [p + "_pos"], name=p + "_pos"),
			helper.make_node("Sub", [x, p + "_pos"], [p + "_neg"], name=p + "_neg"),
			helper.make_node("Mul", [p + "_neg", alpha.name], [p + "_mul"], name=p + "_mul"),
			helper.make_node("Add", [p + "_pos", p + "_mul"], list(n.output), name=p),
		]
	del g.node[:]
	g.node.extend(nodes)
	del g.value_info[:]
	onnx.checker.check_model(m)
	onnx.save(m, dst)


if __name__ == "__main__":
	main(*sys.argv[1:3])
