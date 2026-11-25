# immich-server for RDK X5. Context prepared by server/build.sh:
#   libavcodec.so.61   jellyfin-ffmpeg + patches/0001: VPU/JPU codecs behind
#                      libx264/libx265 and the default decoders, same ABI
#   libavfilter.so.10  + patches/0002: scale/transpose on the 2D GPU
#                      (off unless X5_N2D_FILTERS=1)
#   x5thumb            photo previews: JPU (JPEG) / VPU (HEIC) + 2D GPU
#   media.repository.js  immich server, decodeImage() tries x5thumb first
#   x5/                RDK X5 runtime libraries and VPU firmware
ARG BASE=ghcr.io/immich-app/immich-server:v3.4.2
FROM ${BASE}
COPY libavcodec.so.61 libavfilter.so.10 /usr/lib/jellyfin-ffmpeg/lib/
COPY x5/ /usr/local/lib/x5/
COPY x5thumb /usr/local/bin/x5thumb
COPY media.repository.js /usr/src/app/server/dist/repositories/
RUN echo /usr/local/lib/x5 > /etc/ld.so.conf.d/x5.conf && ldconfig
ENV LIBMM_FM_PATH=/usr/local/lib/x5/firmware/ \
	IMMICH_X5THUMB=/usr/local/bin/x5thumb \
	X5_N2D_FILTERS=0
