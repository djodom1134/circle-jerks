#!/usr/bin/env bash
# Consecutive-failure gate for the cron health check.
#
# Old cron behavior: any single failed healthz → restart api/web/caddy → 30-60s
# of downtime → DO uptime monitor alerts. Even a transient slow scan caused a
# false-positive restart that *itself* created the downtime DO was alerting on.
#
# This wrapper only triggers the restart action when the check has failed
# CONSECUTIVELY for $HEALTH_FAILURE_THRESHOLD ticks (default 2). One bad tick
# self-heals; sustained badness still recovers via restart.
#
# Usage (from cron):
#   ./scripts/health_gate.sh \
#     "./scripts/check_production_health.sh --local" \
#     "docker compose -f docker-compose.prod.yml restart api web caddy"

set -uo pipefail

STATE_FILE="${HEALTH_STATE_FILE:-/var/run/circlejerk-health-failures}"
THRESHOLD="${HEALTH_FAILURE_THRESHOLD:-2}"

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <check-command> <recovery-command>" >&2
  exit 2
fi

CHECK_CMD="$1"
RECOVERY_CMD="$2"
NOW="$(date '+%Y-%m-%dT%H:%M:%S')"

# Make sure the state dir exists (e.g. fresh boot wiped /var/run).
mkdir -p "$(dirname "$STATE_FILE")"

if eval "$CHECK_CMD"; then
  # Healthy — clear any prior failure streak.
  echo 0 > "$STATE_FILE"
  exit 0
fi

failures=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
failures=$((failures + 1))
echo "$failures" > "$STATE_FILE"
echo "[$NOW] health check failed (consecutive: $failures/$THRESHOLD)"

if [ "$failures" -ge "$THRESHOLD" ]; then
  echo "[$NOW] consecutive failure threshold reached; running recovery action"
  eval "$RECOVERY_CMD"
  # Reset so the next failure starts a fresh streak.
  echo 0 > "$STATE_FILE"
fi
