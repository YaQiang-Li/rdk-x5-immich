#!/bin/sh
# SPDX-License-Identifier: MIT
# Check that this RDK X5 can run the images (read-only, run as root).
# usage: deploy/check-board.sh
fail=0
ok() { printf '  ok    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1"; fail=1; }
warn() { printf '  warn  %s\n' "$1"; }

echo "board"
model=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null)
case $model in
*X5*|*x5*) ok "$model" ;;
*) bad "not an RDK X5 (device-tree model: ${model:-unknown})" ;;
esac
[ -f /etc/version ] && ok "system version $(cat /etc/version)"

echo "devices"
for d in vpu jpu ion nano2d bpu bpu_core0; do
	if [ -c /dev/$d ]; then ok "/dev/$d"; else bad "/dev/$d missing"; fi
done

echo "vendor libraries"
for f in /usr/hobot/lib/libmultimedia.so.1 /usr/hobot/lib/libalog.so.1 \
	/usr/hobot/lib/libhbmem.so.1 /usr/hobot/lib/libNano2D.so \
	/usr/hobot/lib/libNano2Dutil.so /usr/hobot/lib/firmware/libmm/chagall.bin \
	/usr/lib/libdnn.so /usr/lib/libhbrt_bayes_aarch64.so; do
	if [ -e "$f" ]; then ok "$f"; else bad "$f missing"; fi
done
for h in /usr/include/hb_media_codec.h /usr/include/GC820/nano2D.h; do
	[ -e "$h" ] || warn "$h missing: needed only to build the images"
done

echo "memory"
mem=$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo)
if [ "$mem" -ge 3500 ]; then ok "${mem} MB RAM"
else warn "${mem} MB RAM: keep the thumbnail concurrency low (see docs)"; fi
ion=/sys/kernel/debug/ion/heaps/cma_reserved
if [ -r $ion ]; then
	awk '/heap total size/ {t = $NF} /^ +total +[0-9]+$/ {u = $2}
		END {printf "  ok    ION cma_reserved %d MB, %d MB used\n",
			t / 1048576, u / 1048576}' $ion
else
	warn "cannot read $ion (debugfs): ION size not checked"
fi

echo "docker"
if docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
	ok "docker $(docker version --format '{{.Server.Version}}')"
else
	bad "docker not running"
fi
docker compose version >/dev/null 2>&1 || bad "docker compose plugin missing"

[ $fail = 0 ] && echo "all checks passed" || echo "some checks failed"
exit $fail
