# SPDX-License-Identifier: MIT
"""Bisect where hb_mapper quantization diverges, on sub-graphs of a model.

For each cut tensor: extract input -> cut, convert with a tiny max
calibration, then compare quantized vs float (host simulator) on samples.
"""
import os
import subprocess
import sys

import numpy as np

import yaml

EXTRA = yaml.safe_load(os.environ.get("CALIB_EXTRA", "{}"))
NODE_INT16 = [n for n in os.environ.get("NODE_INT16", "").split(",") if n]
ROOT = os.getcwd()  # the work dir: onnx/, onnx_fixed/, calib/, build/


def build(onnx_path, out_dir, base_cfg, calib):
	cfg = yaml.safe_load(open(base_cfg))
	cfg["model_parameters"]["onnx_model"] = onnx_path
	cfg["model_parameters"]["working_dir"] = out_dir
	cfg["calibration_parameters"]["cal_data_dir"] = calib
	cfg["calibration_parameters"]["calibration_type"] = "max"
	cfg["calibration_parameters"].pop("run_on_cpu", None)
	cfg["calibration_parameters"].update(EXTRA)
	if NODE_INT16:  # only the nodes this sub-graph still has
		import onnx
		names = {n.name for n in onnx.load(onnx_path, load_external_data=False).graph.node}
		info = {n: {"InputType": "int16", "OutputType": "int16"} for n in NODE_INT16 if n in names}
		cfg["model_parameters"]["node_info"] = info
	path = os.path.join(out_dir + ".yaml")
	os.makedirs(out_dir, exist_ok=True)
	yaml.safe_dump(cfg, open(path, "w"))
	log = open(out_dir + ".log", "w")
	return subprocess.call(["hb_mapper", "makertbin", "--config", path,
		"--model-type", "onnx"], stdout=log, stderr=log, cwd=ROOT)


def compare(out_dir, inputs, mean, std):
	import logging
	logging.disable(logging.WARNING)
	from horizon_tc_ui import HB_ONNXRuntime
	f = HB_ONNXRuntime(model_file=f"{out_dir}/model_original_float_model.onnx")
	q = HB_ONNXRuntime(model_file=f"{out_dir}/model_quantized_model.onnx")
	c = HB_ONNXRuntime(model_file=f"{out_dir}/model_calibrated_model.onnx")
	cc = []
	cs = []
	for x in inputs:
		nchw = x.transpose(0, 3, 1, 2)
		a = f.run(f.output_names, {f.input_names[0]: nchw.astype(np.float32)})[0]
		b = q.run(q.output_names, {q.input_names[0]:
			(nchw.astype(np.int16) - 128).astype(np.int8)})[0]  # int8 input
		k = c.run(c.output_names, {c.input_names[0]: nchw.astype(np.float32)})[0]
		a, b, k = a.ravel(), b.ravel(), k.ravel()
		cs.append(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))
		cc.append(a @ k / (np.linalg.norm(a) * np.linalg.norm(k) + 1e-12))
	return f"quantized {np.mean(cs):.4f} calibrated {np.mean(cc):.4f}"


def _is_int8(node):
	return node.type in (3, "tensor(int8)")


def main(src, base_cfg, calib, evalset, *cuts):
	import onnx
	m = onnx.load(src, load_external_data=False)
	inp = m.graph.input[0].name
	for cut in cuts:
		tag = "cut_" + cut.replace("/", "_") + os.environ.get("TAG", "")
		sub = f"{ROOT}/build/bisect/{tag}.onnx"
		os.makedirs(os.path.dirname(sub), exist_ok=True)
		onnx.utils.extract_model(src, sub, [inp], [cut])
		out_dir = f"{ROOT}/build/bisect/{tag}"
		ret = build(sub, out_dir, base_cfg, calib)
		cos = None
		if ret == 0:  # horizon_tc_ui and onnx clash in one process
			res = subprocess.run([sys.executable, __file__, "--compare", out_dir,
				evalset], capture_output=True, text=True)
			cos = res.stdout.strip().splitlines()[-1] if res.stdout else res.stderr[-300:]
		print(f"RES cut {cut}: build {ret}, quantized-vs-float cos {cos}", flush=True)


if __name__ == "__main__":
	if sys.argv[1] == "--compare":
		print(compare(sys.argv[2], np.load(sys.argv[3])["inputs"][:5], 0, 1))
	else:
		main(*sys.argv[1:])
