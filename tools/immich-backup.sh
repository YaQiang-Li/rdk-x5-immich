#!/bin/sh
# SPDX-License-Identifier: MIT
# Daily immich backup: database dump (kept 30 days) + incremental copy of
# the originals to a second disk. Thumbnails and encoded videos are not
# backed up: immich regenerates them. Files deleted in immich are kept on
# the backup disk (no --delete), so a mistaken deletion can be undone.
#
# usage: LIBRARY=<UPLOAD_LOCATION> BACKUP_ROOT=<mount point> immich-backup.sh
# cron:  30 4 * * * LIBRARY=... BACKUP_ROOT=... /path/immich-backup.sh
set -u

: "${LIBRARY:?set LIBRARY to immich UPLOAD_LOCATION}"
: "${BACKUP_ROOT:?set BACKUP_ROOT to the backup disk mount point}"
DB_DIR=$BACKUP_ROOT/db
MIN_FREE_GB=${MIN_FREE_GB:-20}

echo "===== immich backup $(date '+%F %T') ====="
if ! mountpoint -q "$BACKUP_ROOT"; then
	echo "FATAL: $BACKUP_ROOT is not mounted"
	exit 1
fi

mkdir -p "$DB_DIR"
dump=$DB_DIR/immich-$(date '+%Y%m%d-%H%M').dump
if ! docker exec immich_postgres pg_dump -U postgres -Fc immich > "$dump" ||
	[ ! -s "$dump" ]; then
	echo "FATAL: pg_dump failed"
	exit 1
fi
echo "db dump: $dump ($(du -h "$dump" | cut -f1))"
find "$DB_DIR" -name 'immich-*.dump' -mtime +30 -delete

rsync -a --stats --exclude=thumbs --exclude=encoded-video \
	--exclude=lost+found "$LIBRARY/" "$BACKUP_ROOT/library/" |
	grep -E "files transferred|Total transferred file size"

free=$(df -BG --output=avail "$BACKUP_ROOT" | tail -1 | tr -dc '0-9')
echo "backup disk free: ${free}G"
[ "$free" -lt "$MIN_FREE_GB" ] && echo "WARN: less than ${MIN_FREE_GB}G free"
echo "===== done $(date '+%F %T') ====="
