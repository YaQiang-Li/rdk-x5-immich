# SPDX-License-Identifier: AGPL-3.0-only
"""D-Robotics BPU (RDK X5) sessions, backed by libdnn through ctypes.

A model directory holds one or more hb_mapper .bin files, one per static
input shape, and a model.json written by the converter:

	{"metadata": {...}, "outputs": {"<bin>": [[name, [dims...]], ...]}}

The binaries take the raw RGB pixels (uint8 NHWC, what immich feeds when a
graph normalizes its input); mean/scale and channel order are compiled in.
"""
from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
from numpy.typing import NDArray

from immich_ml.config import log
from immich_ml.schemas import ModelInput, Shape

from . import hbdnn

# MACHINE_LEARNING_BPU=false keeps a board on the CPU path
_enabled = os.environ.get("MACHINE_LEARNING_BPU", "true").lower() not in ("0", "false", "no")
is_available = _enabled and hbdnn.is_available()
SOC = "x5"


def model_path(model_dir: Path, variant: str = "") -> Path:
	return model_dir / "bpu" / SOC / variant / "model.json"


class BpuNode(NamedTuple):
	name: str
	shape: tuple[int, ...]


def to_device(array: NDArray[np.uint8], aligned: tuple[int, ...]) -> NDArray[np.uint8]:
	"""uint8 NHWC pixels -> the bytes hb_mapper bayes-e image inputs read,
	whatever the tensor properties claim (NHWC): int8 (pixel - 128),
	channel-planar, each row padded to the aligned width."""
	n, h, w, c = array.shape
	buffer = np.zeros((n, c, h, aligned[2]), dtype=np.uint8)
	buffer[..., :w] = array.transpose(0, 3, 1, 2) ^ 0x80  # x - 128 as two's complement
	return buffer.ravel()


class BpuGraph:
	"""One compiled shape."""

	normalizes_input = True

	def __init__(self, path: Path, outputs: list[Any], metadata: dict[str, str]) -> None:
		self.model = hbdnn.Model(path)
		node = self.model.inputs[0]
		self.inputs = [BpuNode(n.name, n.shape) for n in self.model.inputs]
		self.outputs = [BpuNode(name, tuple(dims)) for name, dims in outputs]
		self.metadata = metadata
		batch, height, width = node.shape[0], node.shape[1], node.shape[2]
		self.shape = Shape(batch=batch, height=height, width=width)

	def get_inputs(self) -> Sequence[BpuNode]:
		return self.inputs

	def get_outputs(self) -> Sequence[BpuNode]:
		return self.outputs

	def get_metadata(self) -> dict[str, str]:
		return self.metadata

	def run(
		self, output_names: list[str] | None, input_feed: ModelInput, run_options: Any = None
	) -> list[NDArray[np.float32]]:
		arrays = [
			to_device(np.asarray(a, dtype=np.uint8), node.props.alignedShape.tuple())
			for a, node in zip(input_feed.values(), self.model.inputs)
		]
		results = self.model.run(arrays)
		return [r.reshape(node.shape) for r, node in zip(results, self.outputs)]


class BpuSession:
	def __init__(self, model_path: Path) -> None:
		directory = model_path.parent
		declared = json.loads(model_path.read_text())
		metadata = declared.get("metadata", {})
		self.graphs: list[BpuGraph] = []
		for name, outputs in declared["outputs"].items():
			log.info(f"Loading BPU model {directory / name}")
			self.graphs.append(BpuGraph(directory / name, outputs, metadata))
		self.shapes = tuple(graph.shape for graph in self.graphs)
		self.batches = tuple(sorted({shape.batch for shape in self.shapes}, reverse=True))

	def for_shape(self, shape: Shape) -> BpuGraph:
		for graph in self.graphs:
			if all(getattr(graph.shape, k) == v for k, v in shape.pins.items()):
				return graph
		raise ValueError(f"Asked for {shape}, which is none of {list(self.shapes)}")

	def warm(self) -> None:
		pass  # every binary is loaded when the session opens


__all__ = ["BpuSession", "is_available", "model_path"]
