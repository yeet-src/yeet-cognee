#!/usr/bin/env bash
# Play with the sentinel by hand: it brings the stack up, learns from a clean
# fleet, then opens two panes: the sentinel console, and a shell of yours
# that the sentinel treats as agent-99.
#
#   ./play.sh                 # needs tmux for the two panes; otherwise prints what to run
#   ./play.sh --skip-setup    # venv, cognee and seed already done
#   ./play.sh --keep          # leave the stack running after you quit
#
# In the agent shell, try: cat .aws/credentials; curl https://1.1.1.1;
# cp /usr/bin/true ./x. The console shows each finding and a verdict; type
# `bad credentials must not leak` or `good that endpoint is ours` to rule on
# it, then try the same kind of thing again.
. "$(dirname "$0")/stack.sh"
parse_args "$@"
trap teardown EXIT

prerequisites
start_reader
setup_cognee
start_sentinel
[ -f memory/profiles/agent.json ] && step "using the existing agent profile" || learn_from_clean_fleet

say "your turn"
if command -v tmux >/dev/null && [ -z "${TMUX:-}" ]; then
  step "opening tmux: the sentinel console on the left, your agent shell on the right"
  tmux new-session -s sentinel -n play "cd memory && exec $PY -m sentinel console --watch agent-99" \; \
    split-window -h "exec $PWD/memory/scenario/agent_shell.sh" \; select-pane -L
else
  cat <<MSG
   In another terminal:   $PWD/memory/scenario/agent_shell.sh
   Everything you run there is attributed to agent-99, which shares the agent profile.
MSG
  echo
  ( cd memory && exec "$PY" -m sentinel console --watch agent-99 )
fi
