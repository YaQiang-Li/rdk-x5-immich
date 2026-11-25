# SPDX-License-Identifier: MIT
"""Lay converted .bin files out the way immich_ml's BpuSession reads them.

<out>/<task>/<model>/<type>/bpu/x5/<label>/{model.json, *.bin}

Each --bin is "<path.bin>=<static.onnx>": the static ONNX the .bin was
converted from, whose (pinned) output shapes immich's postprocessing expects.

Usage:
  python export_bpu.py --out pack --task facial-recognition --model buffalo_l \
		--type detection --bin build/facial-recognition_detection/model.bin=onnx_fixed/face_det.onnx
"""
import argparse
import json
import os
import shutil

import onnx


def output_shapes(path):
	g = onnx.load(path, load_external_data=False).graph
	return [[o.name, [d.dim_value for d in o.type.tensor_type.shape.dim]]
		for o in g.output]


def input_tag(path):
	dims = [d.dim_value for d in onnx.load(path, load_external_data=False)
		.graph.input[0].type.tensor_type.shape.dim]
	n, _, h, w = dims
	return f"batch{n or 1}_height{h}_width{w}"  # a free batch is compiled as 1


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--out", required=True)
	ap.add_argument("--task", required=True)
	ap.add_argument("--model", required=True)
	ap.add_argument("--type", required=True)
	ap.add_argument("--label", default="")
	ap.add_argument("--bin", action="append", required=True)
	ap.add_argument("--metadata-from", help="ONNX whose metadata_props to keep")
	a = ap.parse_args()
	dst = os.path.join(a.out, a.task, a.model, a.type, "bpu", "x5", a.label)
	os.makedirs(dst, exist_ok=True)
	declared = {"metadata": {}, "outputs": {}}
	if a.metadata_from:
		m = onnx.load(a.metadata_from, load_external_data=False)
		declared["metadata"] = {p.key: p.value for p in m.metadata_props}
	for spec in a.bin:
		bin_path, onnx_path = spec.split("=")
		name = input_tag(onnx_path) + ".bin"
		shutil.copyfile(bin_path, os.path.join(dst, name))
		declared["outputs"][name] = output_shapes(onnx_path)
	with open(os.path.join(dst, "model.json"), "w", encoding="utf-8") as f:
		json.dump(declared, f, ensure_ascii=False, indent=1)
	print(dst, sorted(declared["outputs"]), list(declared["metadata"]))


if __name__ == "__main__":
	main()
