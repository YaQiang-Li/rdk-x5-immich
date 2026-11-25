# SPDX-License-Identifier: MIT
"""Compare two immich /predict facial-recognition responses (a vs b)."""
import base64
import json
import sys

import numpy as np


def box(f):
	r = f["boundingBox"]
	return np.array([r["x1"], r["y1"], r["x2"], r["y2"]], float)


def emb(f):
	e = f["embedding"]
	if isinstance(e, str) and e.startswith("["):
		return np.array(json.loads(e), float)
	return np.frombuffer(base64.b64decode(e), np.float32).astype(float)


def iou(a, b):
	inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
	area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
	return inter / (area(a) + area(b) - inter)


def main(pa, pb):
	fa = json.load(open(pa))["facial-recognition"]
	fb = json.load(open(pb))["facial-recognition"]
	print(f"faces {pa}: {len(fa)}, {pb}: {len(fb)}")
	ious, coss = [], []
	for x in fb:
		best = max(fa, key=lambda y: iou(box(x), box(y)))
		ious.append(iou(box(x), box(best)))
		ea, eb = emb(x), emb(best)
		coss.append(ea @ eb / np.linalg.norm(ea) / np.linalg.norm(eb))
	print(f"matched IoU mean {np.mean(ious):.3f} min {np.min(ious):.3f}; "
		  f"embedding cos mean {np.mean(coss):.4f} min {np.min(coss):.4f}")


if __name__ == "__main__":
	main(*sys.argv[1:3])
