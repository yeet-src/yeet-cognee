# Shared by demo.sh and play.sh: bring the sentinel stack up and down.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

AGENTS=${AGENTS:-5}
LEARN=${LEARN:-45}
KEEP=0
SETUP=1
PY="$PWD/memory/.venv/bin/python"
SERVER="${SENTINEL_SERVER_URL:-http://127.0.0.1:9495}"
READER="${SENTINEL_READER_URL:-http://127.0.0.1:9490}"
LOG=.demo
mkdir -p "$LOG"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
step() { printf '   %s\n' "$*"; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

parse_args() {
  for arg in "$@"; do
    case "$arg" in
      --keep) KEEP=1 ;;
      --skip-setup) SETUP=0 ;;
      --agents) ;;
      [0-9]*) AGENTS="$arg" ;;
      -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
      *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
  done
}

teardown() {
  [ "$KEEP" = 1 ] && return
  say "tearing down"
  [ -n "${FLEET_PID:-}" ] && kill "$FLEET_PID" 2>/dev/null && step "fleet stopped"
  [ -n "${SERVE_PID:-}" ] && kill "$SERVE_PID" 2>/dev/null && step "sentinel stopped"
  yeet service remove -f sentinel-sysgraph >/dev/null 2>&1 && step "yeet reader removed" || true
}

sentinel() { ( cd memory && "$PY" -m sentinel "$@" ); }

fleet() {
  ( cd memory && exec "$PY" scenario/fleet.py --agents "$AGENTS" --cycle 15 "$@" ) > "$LOG/fleet.log" 2>&1 &
  FLEET_PID=$!
  sleep 2; sed -n '/^fleet of/q;p' "$LOG/fleet.log" | sed 's/^/   /'
}

prerequisites() {
  say "checking prerequisites"
  command -v yeet >/dev/null || die "yeet is not installed: curl -fsSL https://yeet.cx | sh"
  yeet service list >/dev/null 2>&1 || die "yeetd is not running"
  step "yeet $(yeet --version | awk '{print $2}') with a running daemon"
  [ -f memory/.env ] || { cp memory/.env.example memory/.env; step "memory/.env created from .env.example"; }
  set -a; . memory/.env; set +a
  if [ "${LLM_PROVIDER:-ollama}" = "ollama" ]; then
    base="${LLM_ENDPOINT:-http://localhost:11434}"; base="${base%/}"; base="${base%/v1}"
    curl -sf -m 3 "$base/api/tags" >/dev/null || die "no Ollama at $base; start it or set LLM_* in memory/.env"
  fi
  step "LLM ${LLM_PROVIDER:-ollama}:${LLM_MODEL:-qwen3:8b}"
}

start_reader() {
  say "applying the yeet reader"
  yeet service apply -y -f yeet-service/service.toml >/dev/null 2>&1
  for _ in $(seq 1 30); do curl -sf -m 2 "$READER/activity" >/dev/null && break; sleep 1; done
  curl -sf -m 2 "$READER/activity" >/dev/null || die "the reader did not come up: yeet service tree sentinel-sysgraph"
  step "serving /activity and /reading on $READER"
}

setup_cognee() {
  [ "$SETUP" = 1 ] || return 0
  say "setting up cognee"
  [ -x "$PY" ] || "$(command -v python3.13 || command -v python3.12 || command -v python3)" -m venv memory/.venv
  "$PY" -c "import cognee" 2>/dev/null || { step "installing cognee (a few minutes the first time)"; "$PY" -m pip install -q -r memory/requirements.txt; }
  step "cognee $("$PY" -c 'import importlib.metadata as m; print(m.version("cognee"))')"
  rm -f memory/profiles/*.json memory/rules.json memory/verdicts.jsonl memory/findings.jsonl
}

start_sentinel() {
  say "starting the sentinel$([ "$SETUP" = 1 ] && echo ' (seeding the policy; minutes on a local model)')"
  local seed=""; [ "$SETUP" = 1 ] && seed="--seed --reset"
  ( cd memory && exec "$PY" -m sentinel.serve $seed --watch agent- --every 60 ) > "$LOG/serve.log" 2>&1 &
  SERVE_PID=$!
  for _ in $(seq 1 600); do
    curl -sf -m 2 "$SERVER/healthz" >/dev/null && break
    kill -0 "$SERVE_PID" 2>/dev/null || { tail -20 "$LOG/serve.log"; die "the sentinel exited; see $LOG/serve.log"; }
    sleep 2
  done
  step "sentinel on $SERVER (log: $LOG/serve.log)"
}

learn_from_clean_fleet() {
  say "learning what a well-behaved fleet does ($AGENTS clean agents for ${LEARN}s)"
  fleet --clean --hold "$LEARN"
  wait "$FLEET_PID"; FLEET_PID=""
  sentinel learn --watch agent- --seconds $((LEARN + 5)) | sed 's/^/   /'
}
