#!/usr/bin/env bash
# Run one coder-agent fixture and record it into the sentinel's memory.
#
# Launches the fixture (which holds itself open for a while), waits a moment for
# it to settle, then records several readings of it as one run report. If the
# memory service (sentinel.serve) is running, it records THROUGH the service so
# there is only ever one cognee client; otherwise it records standalone.
#
# Usage:
#   run_scenario.sh baseline benign-001
#   run_scenario.sh unusual  rogue-001  /var/run/docker.sock /proc/1/environ
#
# Arguments after the label (unusual mode only) are paths the fixture opens
# read-only, so they show up as open file descriptors in the sysgraph.

set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:?usage: run_scenario.sh <baseline|unusual> <label> [paths...]}"
LABEL="${2:?missing run label}"
shift 2

PY=.venv/bin/python
HOLD=70
SAMPLES=4
INTERVAL=8
SERVE_URL="${SENTINEL_SERVE_URL:-http://127.0.0.1:9495}"

TOUCH_ARGS=()
for p in "$@"; do
  TOUCH_ARGS+=(--touch "$p")
done

echo ">> launching coder-agent fixture (mode=$MODE, label=$LABEL)"
$PY scenario/coder_agent.py --mode "$MODE" --hold "$HOLD" "${TOUCH_ARGS[@]}" \
  > "/tmp/coder-agent-$LABEL.log" 2>&1 &
FIXTURE_PID=$!
echo ">> fixture running as pid $FIXTURE_PID, log /tmp/coder-agent-$LABEL.log"

sleep 4   # let it open its files and socket before we start reading

if curl -sf "$SERVE_URL/healthz" >/dev/null 2>&1; then
  echo ">> recording via the memory service at $SERVE_URL"
  curl -s "$SERVE_URL/remember" -H 'Content-Type: application/json' \
    -d "{\"watch\":[\"coder_agent.py\"],\"label\":\"$LABEL\",\"samples\":$SAMPLES,\"interval\":$INTERVAL}"
  echo
else
  echo ">> no memory service running; recording standalone"
  $PY -m sentinel.collector \
    --watch coder_agent.py \
    --label "$LABEL" \
    --samples "$SAMPLES" \
    --interval "$INTERVAL"
fi

echo ">> waiting for fixture to exit"
wait "$FIXTURE_PID" 2>/dev/null || true
echo ">> scenario $LABEL complete"
