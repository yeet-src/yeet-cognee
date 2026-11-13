#!/usr/bin/env bash
# A bash the sentinel treats as one of the agents: argv[0] is agent-99, so it
# shares the `agent` profile, and everything run from it is attributed to it.
exec -a agent-99 bash --norc --noprofile -c '
  mkdir -p "$1" && cd "$1" || exit 1
  PS1="agent-99:\w\$ " exec -a agent-99 bash --norc --noprofile
' -- "$HOME/agent-workspace/agent-99"
