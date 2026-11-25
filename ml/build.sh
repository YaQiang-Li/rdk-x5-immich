#!/bin/sh
# SPDX-License-Identifier: MIT
# Build immich-machine-learning:<version>-bpu on an RDK X5 (run as root,
# needs docker). The BPU runtime libraries are copied from the board.
#
# usage: ml/build.sh [base-image]
set -eu

BASE=${1:-ghcr.io/immich-app/immich-machine-learning:v3.4.2}
TAG=immich-machine-learning:${BASE##*:}-bpu
HERE=$(cd "$(dirname "$0")" && pwd)
C=$HERE/docker

mkdir -p "$C/hobot-lib"
cp -L /usr/lib/libdnn.so /usr/lib/libhbrt_bayes_aarch64.so "$C/hobot-lib/"
cp -a /usr/hobot/lib/libcnn_intf.so* /usr/hobot/lib/libhbmem.so* \
	/usr/hobot/lib/libalog.so* "$C/hobot-lib/"
# the overlay replaces upstream files: the build fails on a base image they
# were not written for (see docker/upstream.sha256)
docker build --build-arg BASE="$BASE" -t "$TAG" "$C"
echo "built $TAG"
