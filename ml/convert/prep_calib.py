# SPDX-License-Identifier: MIT
"""Generate hb_mapper calibration data, mirroring immich_ml preprocessing.

Output: float32 NCHW raw files (pre-normalization, 0-255), one per sample,
in the channel order of input_type_train.
"""
import argparse
import glob
import os
import random

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image

ROOT = os.getcwd()  # the work dir: onnx/, onnx_fixed/, calib/, build/
ONNX = os.path.join(ROOT, "onnx")
ARCFACE_DST = np.array(
	[[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
	 [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)


def images(dirs, n, seed=0):
	files = []
	for d in dirs:
		files += glob.glob(os.path.join(d, "**", "*.jp*g"), recursive=True)
	random.Random(seed).shuffle(files)
	return files[:n]


def load_rgb(path):
	return np.asarray(Image.open(path).convert("RGB"))


def letterbox(image, size):
	h, w = image.shape[:2]
	if h > w:
		nh, nw = size, int(size * w / h)
	else:
		nw, nh = size, int(size * h / w)
	canvas = np.zeros((size, size, 3), dtype=np.uint8)
	cv2.resize(image, (nw, nh), dst=canvas[:nh, :nw])
	return canvas, nh / h


def save(out, idx, hwc):
	chw = np.ascontiguousarray(hwc.transpose(2, 0, 1)[None], np.float32)
	chw.tofile(os.path.join(out, f"{idx:04d}.bin"))


def clip_input(path, size=224):
	img = Image.open(path).convert("RGB")
	if img.width < img.height:
		img = img.resize((size, int(img.height / img.width * size)), Image.BICUBIC)
	else:
		img = img.resize((int(img.width / img.height * size), size), Image.BICUBIC)
	left = int(img.size[0] / 2 - size / 2)
	upper = int(img.size[1] / 2 - size / 2)
	return np.asarray(img.crop((left, upper, left + size, upper + size)))


def scrfd(sess, image, min_score=0.5):
	"""Return 5-point landmarks of faces, in original image coordinates."""
	canvas, scale = letterbox(image, 640)
	blob = ((canvas.astype(np.float32) - 127.5) / 128).transpose(2, 0, 1)[None]
	heads = sess.run(None, {sess.get_inputs()[0].name: blob})
	kps_all, scores_all = [], []
	for level, stride in enumerate((8, 16, 32)):
		n = 640 // stride
		ys, xs = np.mgrid[:n, :n]
		centers = np.stack([xs, ys], -1).reshape(-1, 2).astype(np.float32)
		centers = np.repeat(centers * stride, 2, axis=0)
		scores = heads[level].reshape(-1)
		kps = heads[level + 6].reshape(-1, 10) * stride
		kps = np.tile(centers, 5) + kps
		keep = scores >= min_score
		kps_all.append(kps[keep])
		scores_all.append(scores[keep])
	kps = np.concatenate(kps_all) / scale
	scores = np.concatenate(scores_all)
	# crude NMS on landmark centers is enough for calibration
	out = []
	for i in np.argsort(-scores):
		c = kps[i].reshape(5, 2).mean(0)
		if all(np.linalg.norm(c - o.mean(0)) > 10 for o in out):
			out.append(kps[i].reshape(5, 2))
	return out


def align(image, kps):
	m, _ = cv2.estimateAffinePartial2D(kps, ARCFACE_DST, method=cv2.LMEDS)
	return cv2.warpAffine(image, m, (112, 112))


def db_boxes(sess, image, size=736):
	"""Text boxes from PP-OCR det (axis-aligned rotated rects, crude)."""
	h, w = image.shape[:2]
	ratio = min(size / min(h, w), 1.0)
	ch = max(int(round(h * ratio / 32) * 32), 32)
	cw = max(int(round(w * ratio / 32) * 32), 32)
	bgr = cv2.resize(image, (cw, ch))[:, :, ::-1].astype(np.float32)
	blob = (bgr / 127.5 - 1.0).transpose(2, 0, 1)[None]
	prob = sess.run(None, {"x": blob})[0][0, 0]
	mask = (prob > 0.3).astype(np.uint8)
	mask = cv2.dilate(mask, np.ones((2, 2), np.uint8))
	cnts, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
	crops = []
	for c in cnts:
		(cx, cy), (bw, bh), ang = cv2.minAreaRect(c)
		if min(bw, bh) < 4:
			continue
		rect = ((cx / cw * w, cy / ch * h),
				(bw / cw * w + min(bw, bh) * 0.8, bh / ch * h + min(bw, bh) * 0.8),
				ang)
		pts = cv2.boxPoints(rect).astype(np.float32)
		rw, rh = int(max(rect[1])), int(min(rect[1]))
		if rw < 8 or rh < 8:
			continue
		src = pts if rect[1][0] >= rect[1][1] else np.roll(pts, 1, axis=0)
		dst = np.array([[0, rh], [0, 0], [rw, 0], [rw, rh]], np.float32)
		m = cv2.getPerspectiveTransform(src, dst)
		crops.append(cv2.warpPerspective(image, m, (rw, rh)))
	return crops


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("kind", choices=[
		"face_det", "face_rec", "clip", "ocr_det", "ocr_rec"])
	ap.add_argument("--src", nargs="+", required=True)
	ap.add_argument("--out", required=True)
	ap.add_argument("-n", type=int, default=100)
	ap.add_argument("--hw", help="ocr_det canvas HxW, e.g. 736x992")
	ap.add_argument("--width", type=int, help="ocr_rec width")
	a = ap.parse_args()
	os.makedirs(a.out, exist_ok=True)

	if a.kind == "face_det":
		for i, f in enumerate(images(a.src, a.n)):
			save(a.out, i, letterbox(load_rgb(f), 640)[0])
	elif a.kind == "clip":
		for i, f in enumerate(images(a.src, a.n)):
			save(a.out, i, clip_input(f))
	elif a.kind == "face_rec":
		sess = ort.InferenceSession(
			f"{ONNX}/facial-recognition/buffalo_l/detection/model.onnx")
		i = 0
		for f in images(a.src, a.n * 4):
			img = load_rgb(f)
			for kps in scrfd(sess, img)[:3]:
				save(a.out, i, align(img, kps))
				i += 1
				if i >= a.n:
					return
	elif a.kind == "ocr_det":
		ch, cw = map(int, a.hw.split("x"))
		for i, f in enumerate(images(a.src, a.n)):
			img = load_rgb(f)
			r = min(ch / img.shape[0], cw / img.shape[1])
			h, w = int(img.shape[0] * r) // 32 * 32 or 32, int(img.shape[1] * r) // 32 * 32 or 32
			canvas = np.zeros((ch, cw, 3), np.uint8)
			canvas[:h, :w] = cv2.resize(img, (w, h), interpolation=cv2.INTER_LANCZOS4)
			save(a.out, i, canvas[:, :, ::-1])  # train layout is BGR
	elif a.kind == "ocr_rec":
		sess = ort.InferenceSession(
			f"{ROOT}/onnx_fixed/ocr_detection_ir8.onnx")
		i = 0
		for f in images(a.src, a.n * 4):
			for crop in db_boxes(sess, load_rgb(f))[:4]:
				rw = max(1, min(a.width, int(np.ceil(48 * crop.shape[1] / crop.shape[0]))))
				canvas = np.full((48, a.width, 3), 127, np.uint8)
				canvas[:, :rw] = cv2.resize(crop, (rw, 48))
				save(a.out, i, canvas[:, :, ::-1])
				i += 1
				if i >= a.n:
					return


if __name__ == "__main__":
	main()
