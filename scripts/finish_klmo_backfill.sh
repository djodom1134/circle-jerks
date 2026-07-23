#!/bin/bash
# Finish the KLMO year backfill: verify the droplet run merged, pull the DB
# down, then DESTROY the droplet. Safe to run repeatedly — refuses (exit 1)
# until the run is actually done, and never destroys before the DB is retrieved.
set -uo pipefail

DROPLET_ID=586466666
IP=164.90.145.178
K=/Users/d/.ssh/id_ed25519_dodom
KH=/tmp/klmo_known_hosts
H="root@$IP"
SSH=(ssh -i "$K" -o UserKnownHostsFile="$KH" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
DEST=/Users/d/Code/FAA_circle_jerk/data/klmo_history.sqlite3
DO=$(grep -m1 '^DIGITAL_OCEAN_API_KEY=' /Users/d/Code/FAA_circle_jerk/.env | cut -d= -f2)

echo "== progress =="
"${SSH[@]}" "$H" "cd /root/backfill/backend; for p in /root/out/part_*.sqlite3; do [ -f \"\$p\" ] && .venv/bin/python -c \"import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);d=c.execute(\\\"SELECT COUNT(*) FROM backfill_progress WHERE status='complete'\\\").fetchone()[0];print(sys.argv[1].split('/')[-1],'days_done',d)\" \"\$p\"; done; echo; echo -n 'MERGED? '; grep -q MERGED /root/runner.log && grep MERGED /root/runner.log || echo NO"

if ! "${SSH[@]}" "$H" "grep -q MERGED /root/runner.log 2>/dev/null"; then
  echo ">> Run not finished yet (no MERGED line). Not retrieving/destroying. Re-run later."
  exit 1
fi

echo "== run complete. pulling klmo_history.sqlite3 down to $DEST =="
mkdir -p "$(dirname "$DEST")"
rsync -az --partial -e "ssh -i $K -o UserKnownHostsFile=$KH -o StrictHostKeyChecking=accept-new" \
  "$H":/root/out/klmo_history.sqlite3 "$DEST"
if [ ! -s "$DEST" ]; then
  echo ">> ERROR: download failed / empty. NOT destroying droplet."
  exit 2
fi
echo "downloaded: $(ls -lh "$DEST" | awk '{print $5}')"

echo "== destroying droplet $DROPLET_ID =="
code=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE -H "Authorization: Bearer $DO" \
  "https://api.digitalocean.com/v2/droplets/$DROPLET_ID")
echo "DO delete HTTP $code (204 = destroyed)"
echo "== done. Remember to REVOKE the GitHub token. =="
