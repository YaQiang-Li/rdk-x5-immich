#!/bin/sh
# SPDX-License-Identifier: MIT
# Watch an immich host while thumbnails are regenerated from the web UI:
# counts thumbnail files written, CPU, memory, ION, VPU/JPU load.
# usage: thumb_watch.sh <thumbs dir> <label> [max seconds]
# Stops 90 s after the last new file (once activity has started).
T=$1
L=$2
MAX=${3:-3600}
D=/sys/kernel/debug
OUT=/tmp/thumb_watch_$L.log
REF=/tmp/thumb_watch_$L.ref
start=$(date +%s)
first=0
last=0
: > "$OUT"
touch "$REF"
while :; do
	now=$(date +%s)
	n=$(find "$T" -type f -newer "$REF" 2>/dev/null | wc -l)
	cpu=$(top -bn1 | awk '/^%Cpu/{print 100-$8; exit}')
	mem=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
	ion=$(awk '/^ +total +[0-9]/{print int($2/1048576)}' $D/ion/heaps/cma_reserved)
	printf '%s files=%s cpu=%s avail=%sMB ion=%sMB vpu=%s jpu=%s\n' \
		$((now - start)) "$n" "$cpu" "$mem" "$ion" \
		"$(cat $D/vpu/loading)" "$(cat $D/jpu/loading)" >> "$OUT"
	if [ "$n" -gt "${prev:-0}" ]; then
		[ "$first" = 0 ] && first=$now
		last=$now
	fi
	prev=$n
	[ "$first" != 0 ] && [ $((now - last)) -ge 90 ] && break
	[ $((now - start)) -ge "$MAX" ] && break
	sleep 2
done
awk -v f="$first" -v l="$last" -v s="$start" '
	{ split($2, a, "="); split($3, c, "="); split($4, m, "=");
	  split($5, i, "=");
	  t = $1 + s;
	  if (t >= f && t <= l) { cs += c[2]; n++ }
	  if (n0 == 0 || m[2] + 0 < mi) mi = m[2] + 0; n0++;
	  if (i[2] + 0 > io) io = i[2] + 0; files = a[2] }
	END { fmt = "files %d in %d s (first to last write), avg cpu %.0f%%, "
	      fmt = fmt "min MemAvailable %d MB, max ion %d MB\n"
	      printf fmt, files, l - f, (n ? cs / n : 0), mi, io }' "$OUT"
