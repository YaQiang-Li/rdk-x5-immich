#!/bin/sh
# SPDX-License-Identifier: MIT
# Convert immich's buffalo_l (face det + rec) and ViT-B-32__openai (visual)
# to RDK X5 BPU binaries, laid out as immich_ml's BpuSession reads them.
#
# Usage: build_models.sh <work_dir> <wider_val_dir> <coco_dir>
#   work_dir/onnx/  must hold immich's model cache layout:
#     facial-recognition/buffalo_l/{detection,recognition}/model.onnx
#     clip/ViT-B-32__openai/visual/model.onnx
#   Env: OE_VENV  OE 1.2.8 venv (see oe_env.sh)
#        TOOLS_PY a python 3.10 with numpy/opencv/onnx and onnxruntime 1.16
#                 (default: $OE_BASE/bin/python with PYTHONPATH as given)
# Output: work_dir/pack/<task>/<model>/<type>/bpu/x5/{model.json,*.bin}
set -eu

WORK=$(cd "$1" && pwd)
WIDER=$(cd "$2" && pwd)
COCO=$(cd "$3" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
OE_BASE="${OE_BASE:-$HOME/miniconda3/envs/oe_env}"
PY="${TOOLS_PY:-$OE_BASE/bin/python}"

cd "$WORK"
mkdir -p onnx_fixed calib build pack

echo "== 1. static, opset 11 graphs"
"$PY" "$HERE/downgrade.py" onnx/facial-recognition/buffalo_l/detection/model.onnx \
	onnx_fixed/face_det.onnx 1x3x640x640
# already opset 11 and static but for batch, which hb_mapper's input_shape pins
"$PY" "$HERE/prelu_rewrite.py" onnx/facial-recognition/buffalo_l/recognition/model.onnx \
	onnx_fixed/face_rec.onnx
"$PY" "$HERE/downgrade.py" onnx/clip/ViT-B-32__openai/visual/model.onnx \
	onnx_fixed/clip_visual.onnx 1x3x224x224

echo "== 2. calibration data"
"$PY" "$HERE/prep_calib.py" face_det --src "$WIDER" "$COCO" --out calib/face_det -n 120
"$PY" "$HERE/prep_calib.py" face_rec --src "$WIDER" --out calib/face_rec -n 100
"$PY" "$HERE/prep_calib.py" clip --src "$COCO" --out calib/clip -n 100

echo "== 3. hb_mapper (one at a time: calibration needs several GB of RAM)"
# shellcheck source=oe_env.sh
. "$HERE/oe_env.sh"
for name in facial-recognition_detection facial-recognition_recognition clip_visual; do
	cp "$HERE/configs/$name.yaml" "$WORK/$name.yaml"  # relative paths resolve from the yaml
	hb_mapper makertbin --config "$WORK/$name.yaml" --model-type onnx > "build/$name.log" 2>&1 \
		|| { echo "hb_mapper $name failed, see build/$name.log" >&2; exit 1; }
	echo "$name: $(wc -c < "build/$name/model.bin") bytes"
done

echo "== 4. model packs"
"$PY" "$HERE/export_bpu.py" --out pack --task facial-recognition --model buffalo_l --type detection \
	--bin build/facial-recognition_detection/model.bin=onnx_fixed/face_det.onnx
"$PY" "$HERE/export_bpu.py" --out pack --task facial-recognition --model buffalo_l --type recognition \
	--bin build/facial-recognition_recognition/model.bin=onnx_fixed/face_rec.onnx
"$PY" "$HERE/export_bpu.py" --out pack --task clip --model ViT-B-32__openai --type visual \
	--bin build/clip_visual/model.bin=onnx_fixed/clip_visual.onnx
