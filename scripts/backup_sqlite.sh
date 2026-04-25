#!/usr/bin/env bash
set -euo pipefail

ROOT=/srv/circlejerk
SOURCE="$ROOT/data/circlejerk.sqlite3"
DEST="$ROOT/backups/circlejerk-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"

test -f "$SOURCE"
sqlite3 "$SOURCE" ".backup '$DEST'"
gzip -f "$DEST"
find "$ROOT/backups" -name 'circlejerk-*.sqlite3.gz' -mtime +14 -delete
echo "$DEST.gz"

