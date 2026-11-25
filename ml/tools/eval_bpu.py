# SPDX-License-Identifier: MIT
"""Run an eval set on the BPU and compare with the ONNX reference.

Usage: python3 eval_bpu.py model.bin evalset.npz
evalset.npz (from make_ref.py): inputs uint8 NHWC (what immich feeds),
ref<i> the ONNX fp32 outputs of immich's CPU path.
Reports per-output cosine similarity (per sample) and BPU latency.
"""
import sys
import time

import numpy as np

from hbdnn import Model


def to_device(array, aligned):
	"""Same as immich_ml.sessions.bpu.to_device: int8 (pixel - 128),
	channel-planar, rows padded to the aligned width."""
	n, h, w, c = array.shape
	buffer = np.zeros((n, c, h, aligned[2]), dtype=np.uint8)
	buffer[..., :w] = array.transpose(0, 3, 1, 2) ^ 0x80
	return buffer.ravel()


def cosine(a, b):
	a, b = a.reshape(len(a), -1).astype(np.float64), b.reshape(len(b), -1)
	num = (a * b).sum(1)
	den = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1) + 1e-12
	return num / den


def main(model_path, npz_path):
	m = Model(model_path)
	aligned = m.inputs[0].props.alignedShape.tuple()
	data = np.load(npz_path)
	inputs = data["inputs"]
	n_out = len([k for k in data.files if k.startswith("ref")])
	outs = [[] for _ in range(n_out)]
	times = []
	for x in inputs:
		feed = to_device(x, aligned)
		t = time.perf_counter()
		res = m.run([feed])
		times.append(time.perf_counter() - t)
		for i, r in enumerate(res):
			outs[i].append(r.reshape(data[f"ref{i}"].shape[1:]))
	print(f"{model_path}: {len(inputs)} samples, BPU run p50 "
		  f"{np.median(times[1:]) * 1000:.2f} ms")
	for i in range(n_out):
		c = cosine(np.stack(outs[i]), data[f"ref{i}"])
		print(f"  out{i} {m.outputs[i].name}: cosine mean {c.mean():.5f} "
			  f"min {c.min():.5f} p5 {np.percentile(c, 5):.5f}")
	np.savez_compressed(npz_path.replace(".npz", ".bpu.npz"),
		**{f"out{i}": np.stack(o) for i, o in enumerate(outs)})


if __name__ == "__main__":
	main(*sys.argv[1:3])
