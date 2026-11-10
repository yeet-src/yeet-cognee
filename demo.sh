#!/usr/bin/env bash
# The whole demo in one command: the yeet reader, the cognee memory service,
# a fleet of coding agents (most well behaved, two not), and the sentinel's
# verdicts about them.
#
#   ./demo.sh                 # run it all, then tear it down
#   ./demo.sh --keep          # leave the services and the fleet running to ask more
#   ./demo.sh --agents 5      # a smaller fleet
#   ./demo.sh --skip-setup    # venv and seed already done
#
# Needs: Linux, yeet 0.25+ with yeetd running, python3 (3.10 to 3.14), and the
# LLM + embeddings cognee is pointed at in memory/.env (default: a local Ollama
# with qwen3:8b and nomic-embed-text).
set -euo pipefail
cd "$(dirname "$0")"

AGENTS=10
HOLD=3600
KEEP=0
SETUP=1
for arg in "$@"; do
  case "$arg" in
    --keep) KEEP=1 ;;
    --skip-setup) SETUP=0 ;;
    --agents=*) AGENTS="${arg#*=}" ;;
    --agents) ;;  # value follows
    [0-9]*) AGENTS="$arg" ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

PY="$PWD/memory/.venv/bin/python"
SERVE_URL="${SENTINEL_SERVE_URL:-http://127.0.0.1:9495}"
READ_URL="${SENTINEL_READING_URL:-http://127.0.0.1:9490/reading}"
LOG=.demo
mkdir -p "$LOG"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
step() { printf '   %s\n' "$*"; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

cleanup() {
  [ "$KEEP" = 1 ] && return
  say "tearing down"
  [ -n "${FLEET_PID:-}" ] && kill "$FLEET_PID" 2>/dev/null && step "fleet stopped"
  [ -n "${SERVE_PID:-}" ] && kill "$SERVE_PID" 2>/dev/null && step "memory service stopped"
  yeet service remove -f sentinel-sysgraph >/dev/null 2>&1 && step "yeet reader removed" || true
}
trap cleanup EXIT

# ---- 0. prerequisites -----------------------------------------------------
say "checking prerequisites"
command -v yeet >/dev/null || die "yeet is not installed: curl -fsSL https://yeet.cx | sh"
yeet service list >/dev/null 2>&1 || die "yeetd is not running (yeet service list failed)"
step "yeet $(yeet --version | awk '{print $2}') with a running daemon"
if [ ! -f memory/.env ]; then
  cp memory/.env.example memory/.env
  step "memory/.env created from .env.example (edit it to point cognee at your LLM)"
fi
set -a; . memory/.env; set +a
if [ "${LLM_PROVIDER:-ollama}" = "ollama" ]; then
  base="${LLM_ENDPOINT:-http://localhost:11434}"; base="${base%/}"; base="${base%/v1}"
  curl -sf -m 3 "$base/api/tags" >/dev/null || die "no Ollama at $base; start it or set LLM_* in memory/.env to another provider"
  step "Ollama at $base, model ${LLM_MODEL:-qwen3:8b}"
else
  step "LLM provider ${LLM_PROVIDER} (${LLM_MODEL:-?})"
fi

# ---- 1. the yeet reader ---------------------------------------------------
say "applying the yeet reader (sentinel-sysgraph)"
yeet service apply -y -f yeet-service/service.toml >/dev/null
for _ in $(seq 1 30); do curl -sf -m 2 "$READ_URL" >/dev/null && break; sleep 1; done
curl -sf -m 2 "$READ_URL" >/dev/null || die "the reader did not come up; yeet service tree sentinel-sysgraph"
step "serving /reading and /activity on ${READ_URL%/reading}"

# ---- 2. the cognee side ---------------------------------------------------
if [ "$SETUP" = 1 ]; then
  say "setting up the cognee side"
  if [ ! -x "$PY" ]; then
    PYBIN=$(command -v python3.13 || command -v python3.12 || command -v python3)
    "$PYBIN" -m venv memory/.venv
    step "venv created with $("$PY" --version)"
  fi
  "$PY" -c "import cognee" 2>/dev/null || { step "installing cognee (a few minutes the first time)"; "$PY" -m pip install -q -r memory/requirements.txt; }
  step "cognee $("$PY" -c 'import importlib.metadata as m; print(m.version("cognee"))')"
fi

SEED_ARGS=""; [ "$SETUP" = 1 ] && SEED_ARGS="--seed --reset"
if [ "$SETUP" = 1 ]; then
  say "starting the memory service (seeding policy, runbook and known-good history; minutes on a local model)"
else
  say "starting the memory service"
fi
( cd memory && exec "$PY" -m sentinel.serve $SEED_ARGS --no-loop --watch agent- ) > "$LOG/serve.log" 2>&1 &
SERVE_PID=$!
for _ in $(seq 1 600); do
  curl -sf -m 2 "$SERVE_URL/healthz" >/dev/null && break
  kill -0 "$SERVE_PID" 2>/dev/null || { tail -20 "$LOG/serve.log"; die "memory service exited; see $LOG/serve.log"; }
  sleep 2
done
step "memory service on $SERVE_URL (log: $LOG/serve.log)"

# ---- 3. the fleet ---------------------------------------------------------
say "starting $AGENTS coding agents"
( cd memory && exec "$PY" scenario/fleet.py --agents "$AGENTS" --hold "$HOLD" --cycle 15 ) > "$LOG/fleet.log" 2>&1 &
FLEET_PID=$!
sleep 2; sed -n '1,${/^fleet of/q;p}' "$LOG/fleet.log" | sed 's/^/   /'
step "letting them work for 40s so the activity log has a few cycles"
sleep 40

# ---- 4. remember this run (in the background), and ask now ----------------
# A verdict judges the live reading; memory supplies policy, known-good
# history and reviews. Recording this run is for later questions, and on a
# local model cognee can take minutes to extract its graph, so it runs while
# the questions are answered and the script waits for it at the end.
say "recording the fleet's run into memory (in the background)"
curl -s "$SERVE_URL/remember" -H 'Content-Type: application/json' \
  -d '{"watch":["agent-"],"label":"fleet","samples":3,"interval":8,"lead":45}' > "$LOG/remember.json" &
REMEMBER_PID=$!
step "readings are being taken; verdicts follow from the live reading"

ask() {
  local watch="$1"; shift
  local q="$*"
  printf '\n\033[36m$ ask --watch %s "%s"\033[0m\n' "$watch" "$q"
  curl -s "$SERVE_URL/ask" -H 'Content-Type: application/json' \
    -d "$(printf '{"question":%s,"watch":["%s"]}' "$("$PY" -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$q")" "$watch")" \
    | "$PY" -c 'import json,sys; r=json.load(sys.stdin); print(r.get("verdict") or r); print("\n   (verdict id", r.get("id"), ")")'
}

# The same roles fleet.py assigns: agent-04 breaks policy, agent-09 (or the
# last agent of a smaller fleet) is the odd one.
POLICY=agent-04; [ "$AGENTS" -lt 4 ] && POLICY=agent-01
ODD=agent-09; [ "$AGENTS" -lt 9 ] && ODD=$(printf 'agent-%02d' "$AGENTS")

say "the verdicts"
ask agent-01 "Is this agent behaving like a normal coding agent on this host?"
ask "$POLICY" "Is this agent trying to escape its sandbox or breaking the sandbox policy?"
ask "$ODD"    "Is this agent doing anything unusual compared with the other agents and with past runs?"
ask agent-   "Across all the agents running now, which ones stand out, and is each a policy violation or just suspicious?"

say "waiting for the run to be remembered"
wait "$REMEMBER_PID" || true
sed 's/^/   /' "$LOG/remember.json"; echo

say "done"
cat <<MSG
   Review a verdict so memory learns from it:
     cd memory && .venv/bin/python -m sentinel.ask --feedback <id> --correct --note "confirmed, agent-04 is out of bounds"
   Ask your own question while the fleet runs (--keep):
     cd memory && .venv/bin/python -m sentinel.ask --watch agent-09 "what is this agent doing that the others are not?"
MSG
