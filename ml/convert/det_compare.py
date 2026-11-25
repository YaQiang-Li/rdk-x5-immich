# SPDX-License-Identifier: MIT
"""Compare SCRFD detections (immich decode, min_score) between ONNX ref
and BPU outputs: recall/precision of faces at IoU>=0.5, score/landmark drift.
"""
import sys

import cv2
import numpy as np

STRIDES = (8, 16, 32)


def decode(heads, size=640, min_score=0.7):
	scores, boxes, kps = [], [], []
	for level, stride in enumerate(STRIDES):
		n = size // stride
		ys, xs = np.mgrid[:n, :n]
		c = np.stack([xs, ys], -1).reshape(-1, 2).astype(np.float32) * stride
		c = np.repeat(c, 2, axis=0)
		d = heads[level + 3].reshape(-1, 4) * stride
		o = heads[level + 6].reshape(-1, 10) * stride
		scores.append(heads[level].reshape(-1))
		boxes.append(np.concatenate([c - d[:, :2], c + d[:, 2:]], 1))
		kps.append(np.tile(c, 5) + o)
	s, b, k = map(np.concatenate, (scores, boxes, kps))
	m = s >= min_score
	s, b, k = s[m], b[m], k[m]
	wh = np.column_stack([b[:, 0], b[:, 1], b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]])
	keep = np.asarray(cv2.dnn.NMSBoxes(wh.tolist(), s.tolist(), 0.0, 0.4),
		dtype=np.intp).reshape(-1)
	return s[keep], b[keep], k[keep]


def iou(a, b):
	x1 = np.maximum(a[:, None, 0], b[None, :, 0])
	y1 = np.maximum(a[:, None, 1], b[None, :, 1])
	x2 = np.minimum(a[:, None, 2], b[None, :, 2])
	y2 = np.minimum(a[:, None, 3], b[None, :, 3])
	inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
	area = lambda r: (r[:, 2] - r[:, 0]) * (r[:, 3] - r[:, 1])
	return inter / (area(a)[:, None] + area(b)[None] - inter + 1e-9)


def main(ref_path, bpu_path, min_score=0.7):
	ref, bpu = np.load(ref_path), np.load(bpu_path)
	n = len(ref["inputs"])
	tp = n_ref = n_bpu = 0
	ious, dscore, dkps = [], [], []
	for i in range(n):
		rs, rb, rk = decode([ref[f"ref{j}"][i] for j in range(9)], min_score=min_score)
		bs, bb, bk = decode([bpu[f"out{j}"][i] for j in range(9)], min_score=min_score)
		n_ref += len(rs)
		n_bpu += len(bs)
		if len(rs) and len(bs):
			m = iou(rb, bb)
			for r in range(len(rs)):
				j = m[r].argmax()
				if m[r, j] >= 0.5:
					tp += 1
					ious.append(m[r, j])
					dscore.append(abs(rs[r] - bs[j]))
					size = rb[r, 2] - rb[r, 0]
					dkps.append(np.abs(rk[r] - bk[j]).mean() / size)
	print(f"min_score {min_score}: faces ref {n_ref}, bpu {n_bpu}, matched {tp} "
		  f"(recall {tp / max(n_ref, 1):.3f}, precision {tp / max(n_bpu, 1):.3f})")
	if ious:
		print(f"  matched IoU mean {np.mean(ious):.4f} min {np.min(ious):.4f}; "
			  f"|dscore| mean {np.mean(dscore):.4f}; "
			  f"landmark err/face size mean {np.mean(dkps):.4f}")


if __name__ == "__main__":
	main(sys.argv[1], sys.argv[2], *map(float, sys.argv[3:4]))
