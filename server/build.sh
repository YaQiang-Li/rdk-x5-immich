#!/bin/sh
# SPDX-License-Identifier: MIT
# Build immich-server:<version>-x5 on an RDK X5 (run as root, needs docker
# and ~6 GB free disk; takes about 30 minutes).
#
# The official image is the build root: jellyfin-ffmpeg is rebuilt inside
# it with the same version, Debian patches and configuration plus
# --enable-x5mm, so the new libraries match what the image runs.
#
# usage: server/build.sh [base-image]
# env:   WORK=<dir>        build dir (default /var/tmp/rdk-x5-immich)
#        APT_MIRROR=<url>  Debian mirror, e.g. https://mirrors.ustc.edu.cn
set -eu

BASE=${1:-ghcr.io/immich-app/immich-server:v3.4.2}
TAG=immich-server:${BASE##*:}-x5
HERE=$(cd "$(dirname "$0")" && pwd)
W=${WORK:-/var/tmp/rdk-x5-immich}/server
NAME=rdk-x5-immich-build
JF=7.1.4-3
JF_URL=https://github.com/jellyfin/jellyfin-ffmpeg/archive/refs/tags/v$JF.tar.gz
JF_SHA=38fff90f73b3c4f9c3c7270711411a4ec3cbe63b205d4b4a5525bcc532d3d31f
SVT="SVT-AV1-v3.1.2"
SVT_URL=https://gitlab.com/AOMediaCodec/SVT-AV1/-/archive/v3.1.2/$SVT.tar.gz
SVT_SHA=d0d73bfea42fdcc1222272bf2b0e2319e9df5574721298090c3d28315586ecb1
X5LIBS="libmultimedia.so.1 libalog.so.1 libhbmem.so.1 libNano2D.so
libNano2Dutil.so"

mkdir -p "$W/inc" "$W/lib" "$W/patches" "$W/out"
cd "$W"
[ -f jf.tar.gz ] || curl -fL -o jf.tar.gz "$JF_URL"
echo "$JF_SHA  jf.tar.gz" | sha256sum -c -
[ -f svt.tar.gz ] || curl -fL -o svt.tar.gz "$SVT_URL"
echo "$SVT_SHA  svt.tar.gz" | sha256sum -c -
[ -d $SVT ] || tar xf svt.tar.gz
# RDK X5 headers and libraries come from the board (hobot-multimedia-dev)
cp /usr/include/hb_media_*.h /usr/include/hb_mem_*.h inc/
cp -r /usr/include/GC820 inc/
for l in multimedia Nano2D Nano2Dutil; do
	ln -sf "/usr/hobot/lib/lib$l.so" "lib/lib$l.so"
done
cp "$HERE"/patches/*.patch patches/
cp "$HERE/x5thumb/x5thumb.c" .

if ! docker inspect "$NAME" >/dev/null 2>&1; then
	docker run -d --name "$NAME" -u 0:0 --entrypoint sleep -v "$W:/build" \
		-v /usr/hobot/lib:/usr/hobot/lib:ro "$BASE" infinity
	docker exec -u 0:0 "$NAME" sh -c "
		set -e
		if [ -n '${APT_MIRROR:-}' ]; then
			sed -i 's#http://deb.debian.org#${APT_MIRROR:-}#' \
				/etc/apt/sources.list.d/*.sources
		fi
		apt-get update -q
		DEBIAN_FRONTEND=noninteractive apt-get install -y -q \
			--no-install-recommends build-essential pkg-config nasm patchelf \
			libvpx-dev libwebp-dev libdav1d-dev libzvbi-dev zlib1g-dev \
			libmp3lame-dev libopus-dev libtheora-dev libvorbis-dev \
			libx264-dev libx265-dev libfdk-aac-dev libzimg-dev libass-dev \
			ocl-icd-opencl-dev opencl-headers libfontconfig-dev \
			libfreetype-dev libharfbuzz-dev libfribidi-dev libxml2-dev \
			libgmp-dev libgnutls28-dev libchromaprint-dev libbluray-dev \
			libopenmpt-dev"
fi
docker start "$NAME" >/dev/null

echo "building jellyfin-ffmpeg $JF (log: $W/make.log)"
docker exec -u 0:0 "$NAME" sh -c "
	set -e
	cd /build
	rm -rf src && mkdir src && tar xf jf.tar.gz -C src --strip-components=1
	cd src
	grep -vE '^[[:space:]]*(#|\$)' debian/patches/series |
		while read -r p; do patch -p1 -s -N < debian/patches/\$p; done
	for p in /build/patches/*.patch; do patch -p1 -s < \$p; done
	mkdir -p /build/pc
	printf 'Name: SvtAv1Enc\nDescription: headers + image library\n\
Version: 3.1.2\nLibs: -L/usr/lib/jellyfin-ffmpeg/lib -lSvtAv1Enc\n\
Cflags: -I/build/$SVT/Source/API\n' > /build/pc/SvtAv1Enc.pc
	# jellyfin's configuration minus the Rockchip/NVIDIA/VAAPI/DRM parts
	PKG_CONFIG_PATH=/build/pc ./configure --prefix=/usr/lib/jellyfin-ffmpeg \
		--extra-version=Jellyfin --disable-doc --disable-ffplay \
		--disable-static --enable-shared --disable-libxcb --disable-sdl2 \
		--disable-xlib --enable-gpl --enable-version3 --enable-gmp \
		--enable-gnutls --enable-chromaprint --enable-opencl \
		--enable-libxml2 --enable-libass --enable-libfreetype \
		--enable-libfribidi --enable-libfontconfig --enable-libharfbuzz \
		--enable-libbluray --enable-libopenmpt --enable-libzimg \
		--enable-libvpx --enable-libwebp --enable-libdav1d \
		--enable-libzvbi --enable-libmp3lame --enable-libopus \
		--enable-libtheora --enable-libvorbis --enable-libx264 \
		--enable-libx265 --enable-libfdk-aac --enable-libsvtav1 \
		--enable-x5mm --disable-vaapi --disable-vdpau --disable-libdrm \
		--disable-cuda-llvm --disable-ffnvcodec \
		--extra-cflags=-I/build/inc --extra-ldflags=-L/build/lib \
		--extra-ldflags=-Wl,-rpath-link=/usr/hobot/lib \
		--extra-ldflags=-Wl,-rpath-link=/usr/lib/jellyfin-ffmpeg/lib \
		> /build/configure.log 2>&1
	make -j\$(nproc) libavcodec/libavcodec.so.61 \
		libavfilter/libavfilter.so.10 > /build/make.log 2>&1
	cp libavcodec/libavcodec.so.61 libavfilter/libavfilter.so.10 /build/out/
	cd /build
	gcc -O3 -Wall -Iinc -Isrc -o out/x5thumb x5thumb.c -Llib \
		-Wl,-rpath-link=/usr/hobot/lib -lmultimedia -lNano2Dutil -lNano2D \
		-L/usr/lib/jellyfin-ffmpeg/lib \
		-Wl,-rpath,/usr/lib/jellyfin-ffmpeg/lib \
		-lavformat -lavcodec -lavutil -lm
	cd out
	for l in libavcodec.so.61 libavfilter.so.10; do
		patchelf --set-rpath /usr/lib/jellyfin-ffmpeg/lib \$l
		strip --strip-unneeded \$l
	done
	strip x5thumb"

# same exported symbols as the libraries in the base image (ABI check)
C=$W/context
rm -rf "$C" && mkdir -p "$C/x5/firmware"
id=$(docker create "$BASE")
for l in libavcodec.so.61:LIBAVCODEC_61 libavfilter.so.10:LIBAVFILTER_10; do
	f=${l%%:*}
	docker cp "$id:/usr/lib/jellyfin-ffmpeg/lib/$f" "$W/orig.so"
	objdump -T "$W/orig.so" | awk -v v="${l#*:}" '$0 ~ v {print $NF}' |
		sort > "$W/syms.orig"
	objdump -T "$W/out/$f" | awk -v v="${l#*:}" '$0 ~ v {print $NF}' |
		sort > "$W/syms.new"
	cmp "$W/syms.orig" "$W/syms.new"
	echo "$f: exported symbols identical to $BASE" \
		"($(wc -l < "$W/syms.new"))"
done
docker cp "$id:/usr/src/app/server/dist/repositories/media.repository.js" "$C/"
docker rm "$id" >/dev/null
python3 "$HERE/immich/patch_media_repository.py" "$C/media.repository.js"

cp "$HERE/Dockerfile" "$W"/out/libav*.so.* "$W/out/x5thumb" "$C/"
for l in $X5LIBS; do
	cp -L "/usr/hobot/lib/$l" "$C/x5/"
done
cp /usr/hobot/lib/firmware/libmm/chagall.bin "$C/x5/firmware/"
docker build --build-arg BASE="$BASE" -t "$TAG" "$C"

# smoke test: libx264 must land on the VPU
docker run --rm -u 0:0 --entrypoint ffmpeg --device /dev/vpu \
	--device /dev/jpu --device /dev/ion "$TAG" -hide_banner -nostats \
	-f lavfi -i testsrc2=s=1280x720 -frames:v 30 -c:v libx264 -f null - \
	2>&1 | grep "using RDK X5 hardware encoder"
echo "built $TAG"
