# SPDX-License-Identifier: MIT
"""Build an eval set: BPU inputs (uint8 NHWC, the order immich feeds) and
ONNX fp32 reference outputs computed exactly as immich's CPU path would.

Input: calibration-format bins (float32 NCHW 0-255, train channel order).
"""
import argparse
import glob
import os

import numpy as np
import onnxruntime as ort

# kind: (mean, std, train order is BGR while immich feeds RGB)
NORM = {
	"face_det": (127.5, 128.0, False),
	"face_rec": (127.5, 127.5, False),
	"clip": (np.array([0.48145466, 0.4578275, 0.40821073]) * 255,
			 np.array([0.26862954, 0.26130258, 0.27577711]) * 255, False),
	"ocr_det": (127.5, 127.5, True),
	"ocr_rec": (127.5, 127.5, True),
}


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("kind", choices=NORM)
	ap.add_argument("onnx")
	ap.add_argument("bins")
	ap.add_argument("out")
	ap.add_argument("--shape", required=True, help="NCHW, e.g. 1x3x112x112")
	ap.add_argument("--skip", type=int, default=0, help="files used for calib")
	ap.add_argument("-n", type=int, default=100)
	a = ap.parse_args()
	shape = [int(x) for x in a.shape.split("x")]
	mean, std, bgr = NORM[a.kind]
	mean = np.asarray(mean, np.float32).reshape(1, -1, 1, 1)
	std = np.asarray(std, np.float32).reshape(1, -1, 1, 1)
	sess = ort.InferenceSession(a.onnx, providers=["CPUExecutionProvider"])
	name = sess.get_inputs()[0].name
	files = sorted(glob.glob(os.path.join(a.bins, "*.bin")))[a.skip:a.skip + a.n]
	inputs, refs = [], []
	for f in files:
		x = np.fromfile(f, np.float32).reshape(shape)
		refs.append(sess.run(None, {name: (x - mean) / std}))
		nhwc = x.transpose(0, 2, 3, 1)
		if bgr:
			nhwc = nhwc[..., ::-1]
		inputs.append(np.round(nhwc).astype(np.uint8))
	data = {"inputs": np.stack(inputs)}
	for i in range(len(refs[0])):
		data[f"ref{i}"] = np.stack([r[i] for r in refs])
	np.savez_compressed(a.out, **data)
	print(a.out, {k: v.shape for k, v in data.items()})


if __name__ == "__main__":
	main()
