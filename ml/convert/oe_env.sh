#!/bin/sh
# SPDX-License-Identifier: MIT
# Source this to put the OE 1.2.8 (bayes-e) toolchain on PATH.
#   OE_VENV=/path/to/oe128_venv . scripts/convert/oe_env.sh
# OE_VENV: a venv with the OE 1.2.8 wheels (see README "工具链").
# OE_BASE: the conda env it was created from (provides python 3.10 deps).
: "${OE_VENV:?set OE_VENV to the OE 1.2.8 venv}"
OE_BASE="${OE_BASE:-$HOME/miniconda3/envs/oe_env}"
HNN="$OE_VENV/lib/python3.10/site-packages/horizon_nn"
export PATH="$OE_VENV/bin:$OE_BASE/bin:$PATH"
export LD_LIBRARY_PATH="$HNN/horizon_onnxruntime:$OE_BASE/lib:${LD_LIBRARY_PATH:-}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/mpl}"
