# SPDX-License-Identifier: AGPL-3.0-only
"""Minimal ctypes binding of D-Robotics libdnn (hb_dnn.h, X5 / bayes-e).

No dependency on the Python version: only libdnn.so and numpy are needed.
"""
import ctypes
import threading
from pathlib import Path

import numpy as np

MAX_DIMS = 8
# hbDNNDataType
_IMG_TYPES = range(0, 6)  # Y, NV12, NV12_SEPARATE, YUV444, RGB, BGR
_NP_TYPES = {
	6: np.int8, 7: np.uint8,  # S4/U4 are not produced by hb_mapper outputs
	8: np.int8, 9: np.uint8, 10: np.float16, 11: np.int16, 12: np.uint16,
	13: np.float32, 14: np.int32, 15: np.uint32, 16: np.float64,
	17: np.int64, 18: np.uint64,
}
_SCALE = 2  # hbDNNQuantiType
_CACHE_INVALIDATE, _CACHE_CLEAN = 1, 2
_WAIT_FOREVER = 0


class SysMem(ctypes.Structure):
	_fields_ = [
		("phyAddr", ctypes.c_uint64),
		("virAddr", ctypes.c_void_p),
		("memSize", ctypes.c_uint32),
	]


class TensorShape(ctypes.Structure):
	_fields_ = [
		("dimensionSize", ctypes.c_int32 * MAX_DIMS),
		("numDimensions", ctypes.c_int32),
	]

	def tuple(self):
		return tuple(self.dimensionSize[:self.numDimensions])


class QuantiShift(ctypes.Structure):
	_fields_ = [
		("shiftLen", ctypes.c_int32),
		("shiftData", ctypes.POINTER(ctypes.c_uint8)),
	]


class QuantiScale(ctypes.Structure):
	_fields_ = [
		("scaleLen", ctypes.c_int32),
		("scaleData", ctypes.POINTER(ctypes.c_float)),
		("zeroPointLen", ctypes.c_int32),
		("zeroPointData", ctypes.POINTER(ctypes.c_int8)),
	]


class TensorProperties(ctypes.Structure):
	_fields_ = [
		("validShape", TensorShape),
		("alignedShape", TensorShape),
		("tensorLayout", ctypes.c_int32),
		("tensorType", ctypes.c_int32),
		("shift", QuantiShift),
		("scale", QuantiScale),
		("quantiType", ctypes.c_int32),
		("quantizeAxis", ctypes.c_int32),
		("alignedByteSize", ctypes.c_int32),
		("stride", ctypes.c_int32 * MAX_DIMS),
	]


class Tensor(ctypes.Structure):
	_fields_ = [
		("sysMem", SysMem * 4),
		("properties", TensorProperties),
	]


class InferCtrlParam(ctypes.Structure):
	_fields_ = [
		("bpuCoreId", ctypes.c_int32),
		("dspCoreId", ctypes.c_int32),
		("priority", ctypes.c_int32),
		("more", ctypes.c_int32),
		("customId", ctypes.c_int64),
		("reserved1", ctypes.c_int32),
		("reserved2", ctypes.c_int32),
	]


_lib = None


def lib():
	global _lib
	if _lib is None:
		_lib = ctypes.CDLL("libdnn.so")
		_lib.hbDNNGetVersion.restype = ctypes.c_char_p
	return _lib


def is_available():
	try:
		lib()
	except OSError:
		return False
	return Path("/dev/bpu").exists() or Path("/dev/bpu_core0").exists()


def _check(ret, what):
	if ret != 0:
		raise RuntimeError(f"{what} failed: {ret}")


class Node:
	"""An input or output of a model, shaped as the source ONNX declared."""

	def __init__(self, name, props):
		self.name = name
		self.props = props
		self.shape = props.validShape.tuple()
		self.dtype = _NP_TYPES.get(props.tensorType, np.uint8)
		self.is_image = props.tensorType in _IMG_TYPES
		self.strides = tuple(props.stride[:props.validShape.numDimensions])

	def scale(self):
		p = self.props
		if p.quantiType != _SCALE or p.scale.scaleLen == 0:
			return None
		s = np.ctypeslib.as_array(p.scale.scaleData, (p.scale.scaleLen,))
		shape = [1] * len(self.shape)
		shape[p.quantizeAxis] = -1
		return s.astype(np.float32).reshape(shape)


class Model:
	"""One model of a .bin, with its own preallocated tensors.

	run() is serialized per instance; open more instances for parallelism.
	"""

	def __init__(self, path):
		self._lib = lib()
		self.path = str(path)
		self._packed = ctypes.c_void_p()
		names = (ctypes.c_char_p * 1)(self.path.encode())
		_check(self._lib.hbDNNInitializeFromFiles(
			ctypes.byref(self._packed), names, 1), "hbDNNInitializeFromFiles")
		name_list = ctypes.POINTER(ctypes.c_char_p)()
		count = ctypes.c_int32()
		_check(self._lib.hbDNNGetModelNameList(
			ctypes.byref(name_list), ctypes.byref(count), self._packed),
			"hbDNNGetModelNameList")
		self.model_name = name_list[0].decode()
		self._handle = ctypes.c_void_p()
		_check(self._lib.hbDNNGetModelHandle(
			ctypes.byref(self._handle), self._packed, name_list[0]),
			"hbDNNGetModelHandle")
		self.inputs = self._nodes(False)
		self.outputs = self._nodes(True)
		self._in = self._alloc(self.inputs)
		self._out = self._alloc(self.outputs)
		self._lock = threading.Lock()

	def _nodes(self, output):
		kind = "Output" if output else "Input"
		count = ctypes.c_int32()
		_check(getattr(self._lib, f"hbDNNGet{kind}Count")(
			ctypes.byref(count), self._handle), f"hbDNNGet{kind}Count")
		nodes = []
		for i in range(count.value):
			name = ctypes.c_char_p()
			_check(getattr(self._lib, f"hbDNNGet{kind}Name")(
				ctypes.byref(name), self._handle, i), f"hbDNNGet{kind}Name")
			props = TensorProperties()
			_check(getattr(self._lib, f"hbDNNGet{kind}TensorProperties")(
				ctypes.byref(props), self._handle, i),
				f"hbDNNGet{kind}TensorProperties")
			nodes.append(Node(name.value.decode(), props))
		return nodes

	def _alloc(self, nodes):
		tensors = (Tensor * len(nodes))()
		for t, node in zip(tensors, nodes):
			t.properties = node.props
			_check(self._lib.hbSysAllocCachedMem(
				ctypes.byref(t.sysMem[0]), node.props.alignedByteSize),
				"hbSysAllocCachedMem")
		return tensors

	def _view(self, tensor, node):
		"""A numpy view of the valid region of a tensor's memory."""
		size = tensor.properties.alignedByteSize
		buf = (ctypes.c_char * size).from_address(tensor.sysMem[0].virAddr)
		flat = np.frombuffer(buf, dtype=node.dtype)
		return np.lib.stride_tricks.as_strided(
			flat, shape=node.shape, strides=node.strides, writeable=True)

	def run(self, arrays):
		with self._lock:
			for t, node, array in zip(self._in, self.inputs, arrays):
				if array.ndim == 1 and array.nbytes == node.props.alignedByteSize:
					# already laid out as the device reads it
					ctypes.memmove(t.sysMem[0].virAddr, array.ctypes.data, array.nbytes)
				elif array.shape != node.shape:
					raise ValueError(
						f"{node.name}: expects {node.shape}, fed {array.shape}")
				else:
					self._view(t, node)[...] = array
				self._lib.hbSysFlushMem(ctypes.byref(t.sysMem[0]), _CACHE_CLEAN)
			task = ctypes.c_void_p()
			out_ptr = ctypes.pointer(self._out[0])
			ctrl = InferCtrlParam(0, 0, 0, 0, 0, 0, 0)
			_check(self._lib.hbDNNInfer(
				ctypes.byref(task), ctypes.byref(out_ptr), self._in,
				self._handle, ctypes.byref(ctrl)), "hbDNNInfer")
			try:
				_check(self._lib.hbDNNWaitTaskDone(task, _WAIT_FOREVER),
					"hbDNNWaitTaskDone")
			finally:
				self._lib.hbDNNReleaseTask(task)
			results = []
			for t, node in zip(self._out, self.outputs):
				self._lib.hbSysFlushMem(
					ctypes.byref(t.sysMem[0]), _CACHE_INVALIDATE)
				value = np.array(self._view(t, node))
				scale = node.scale()
				if scale is not None:
					value = value.astype(np.float32) * scale
				results.append(value)
			return results

	def release(self):
		for tensors in (getattr(self, "_in", ()), getattr(self, "_out", ())):
			for t in tensors:
				self._lib.hbSysFreeMem(ctypes.byref(t.sysMem[0]))
		self._in = self._out = ()
		if getattr(self, "_packed", None):
			self._lib.hbDNNRelease(self._packed)
			self._packed = None

	def __del__(self):
		self.release()
