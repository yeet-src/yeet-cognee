#!/usr/bin/env bash
# The whole demo with NO kernel and NO fleet: the sentinel judges a recorded
# trace of a coding-agent fleet. Only a local model (for the verdict) is needed
# -- no yeetd, no root, no BPF. This is the quickest way to try it.
#
#   ./replay.sh                 # play the trace into the sentinel console
#   ./replay.sh --speed=3       # play it faster
#   ./replay.sh --rebaseline    # rebuild the baseline from the trace
#
# In the console, when agent-03 opens the Docker socket, rule it:
#   bad the docker socket is off limits
# then watch agent-06 try the same thing -- the verdict now cites your ruling.
. "$(dirname "$0")/stack.sh"
REPLAY="$PWD/memory/scenario/trace.json"
parse_args "$@"
trap teardown EXIT

replay_prerequisites() {
  say "checking prerequisites (no kernel needed)"
  [ -f memory/.env ] || { cp memory/.env.example memory/.env; step "memory/.env created from .env.example"; }
  set -a; . memory/.env; set +a
  [ -x "$PY" ] || "$(command -v python3.13 || command -v python3.12 || command -v python3)" -m venv memory/.venv
  "$PY" -c "import cognee" 2>/dev/null || { step "installing cognee (a few minutes the first time)"; "$PY" -m pip install -q -r memory/requirements.txt; }
  if [ "${LLM_PROVIDER:-ollama}" = "ollama" ]; then
    base="${LLM_ENDPOINT:-http://localhost:11434}"; base="${base%/}"; base="${base%/v1}"
    curl -sf -m 3 "$base/api/tags" >/dev/null || die "no Ollama at $base; start it or point LLM_* in memory/.env at your model"
  fi
  step "python + ${LLM_PROVIDER:-ollama}:${LLM_MODEL:-qwen3:8b} ready"
}

build_replay_baseline() {
  plan_memory
  [ "$RESTORE" = 1 ] && return 0
  say "building a baseline from the trace (seeds the policy, learns the clean fleet; minutes on a local model)"
  rm -f memory/rules.json memory/verdicts.jsonl memory/findings.jsonl memory/profiles/*.json
  ( cd memory && "$PY" -m sentinel.replay build --trace "$REPLAY" ) | sed 's/^/   /'
  RESTORE=1
}

replay_prerequisites
build_replay_baseline
start_sentinel

say "playing the trace -- open the console and watch"
step "agent-03 will break policy; agent-05 will look suspicious; agent-06 repeats agent-03"
step "what cognee remembers is drawn in .demo/memory-graph.html"
( cd memory && exec "$PY" -m sentinel console --watch agent- )
