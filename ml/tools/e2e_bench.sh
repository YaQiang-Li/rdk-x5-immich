#!/bin/sh
# SPDX-License-Identifier: MIT
# End-to-end /predict benchmark: BPU container vs CPU container.
# Usage: e2e_bench.sh <bpu_port> <cpu_port> <face.jpg> <ocr.jpg> [runs]
# Writes resp_<port>_<task>.json (last response) in the current directory.
set -eu

BPU_PORT=$1
CPU_PORT=$2
FACE_IMG=$3
OCR_IMG=$4
RUNS=${5:-5}

FACE='{"facial-recognition":{"detection":{"modelName":"buffalo_l","options":{"minScore":0.7}},'\
'"recognition":{"modelName":"buffalo_l"}}}'
CLIP='{"clip":{"visual":{"modelName":"ViT-B-32__openai"}}}'
OCR='{"ocr":{"detection":{"modelName":"PP-OCRv5_mobile","options":{"maxResolution":736,"minScore":0.5}},'\
'"recognition":{"modelName":"PP-OCRv5_mobile","options":{"minScore":0.8}}}}'

bench() {
	port=$1
	task=$2
	entries=$3
	img=$4
	# first call loads the model, not timed
	curl -sf -o "resp_${port}_${task}.json" -F "entries=$entries" -F "image=@$img" "localhost:$port/predict"
	i=0
	while [ "$i" -lt "$RUNS" ]; do
		curl -sf -o "resp_${port}_${task}.json" -w '%{time_total}\n' \
			-F "entries=$entries" -F "image=@$img" "localhost:$port/predict"
		i=$((i + 1))
	done | awk -v p="$port" -v t="$task" '{s += $1} END {printf "port %s %-5s mean %.3f s (%d runs)\n", p, t, s / NR, NR}'
}

uptime
for port in "$BPU_PORT" "$CPU_PORT"; do
	bench "$port" face "$FACE" "$FACE_IMG"
	bench "$port" clip "$CLIP" "$FACE_IMG"
	bench "$port" ocr "$OCR" "$OCR_IMG"
done
uptime
