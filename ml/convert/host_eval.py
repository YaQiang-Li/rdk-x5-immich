# SPDX-License-Identifier: MIT
"""Host check of a converted model: original-float vs quantized (simulator),
on calibration-format bins. Prints output cosine; for OCR det also the
text-box recall at IoU>=0.5 after DB thresholding (immich thresh 0.3).

Usage: python host_eval.py <build_dir> <bins_dir> <NCHW shape> [det|-] [n] [flip]
"""
import glob
import logging
import os
import sys

import cv2
import numpy as np

logging.disable(logging.WARNING)


def boxes(prob, thresh=0.3, min_size=3):
	mask = (prob > thresh).astype(np.uint8)
	cnts, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
	out = []
	for c in cnts:
		x, y, w, h = cv2.boundingRect(c)
		if min(w, h) >= min_size:
			out.append((x, y, x + w, y + h))
	return np.array(out, np.float32).reshape(-1, 4)


def recall(ref, got):
	if not len(ref):
		return None
	if not len(got):
		return 0.0
	x1 = np.maximum(ref[:, None, 0], got[None, :, 0])
	y1 = np.maximum(ref[:, None, 1], got[None, :, 1])
	x2 = np.minimum(ref[:, None, 2], got[None, :, 2])
	y2 = np.minimum(ref[:, None, 3], got[None, :, 3])
	inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
	area = lambda b: (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
	iou = inter / (area(ref)[:, None] + area(got)[None] - inter)
	return float((iou.max(1) >= 0.5).mean())


def main(build, bins, shape, mode="", n="20", flip="0"):
	from horizon_tc_ui import HB_ONNXRuntime
	shape = [int(v) for v in shape.split("x")]
	f = HB_ONNXRuntime(model_file=f"{build}/model_original_float_model.onnx")
	q = HB_ONNXRuntime(model_file=f"{build}/model_quantized_model.onnx")
	files = sorted(glob.glob(os.path.join(bins, "*.bin")))[-int(n):]
	cos, rec = [], []
	for path in files:
		x = np.fromfile(path, np.float32).reshape(shape)
		a = f.run(f.output_names, {f.input_names[0]: x})[0]
		# quantized input: int8 (pixel - 128), NCHW, in the *runtime* channel
		# order: flip=1 when input_type_rt differs from input_type_train
		xq = x[:, ::-1] if flip == "1" else x
		b = q.run(q.output_names, {q.input_names[0]:
			(np.round(xq) - 128).astype(np.int8)})[0]
		cos.append(a.ravel() @ b.ravel() / (np.linalg.norm(a) * np.linalg.norm(b)))
		if mode == "det":
			r = recall(boxes(a.squeeze()), boxes(b.squeeze()))
			if r is not None:
				rec.append(r)
	print(f"{build}: cosine mean {np.mean(cos):.4f} min {np.min(cos):.4f}")
	if rec:
		print(f"  text-box recall@IoU0.5 mean {np.mean(rec):.4f} min {np.min(rec):.4f}")


if __name__ == "__main__":
	main(*sys.argv[1:])
