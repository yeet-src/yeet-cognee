#!/usr/bin/env bash
# Run one coder-agent fixture and observe it with the sentinel collector.
#
# Launches the fixture (which holds itself open for a while), waits a moment
# for it to settle, then runs the collector to take several readings of it
# and fold them into one run report in cognee.
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

echo ">> collecting $SAMPLES readings with the sentinel"
$PY -m sentinel.collector \
  --watch coder_agent.py \
  --label "$LABEL" \
  --samples "$SAMPLES" \
  --interval "$INTERVAL"

echo ">> waiting for fixture to exit"
wait "$FIXTURE_PID" 2>/dev/null || true
echo ">> scenario $LABEL complete"
