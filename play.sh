#!/usr/bin/env bash
# Play with the sentinel by hand: it brings the stack up, learns from a clean
# fleet, then follows a shell of yours that it treats as agent-99.
#
#   ./play.sh                 # then, in another terminal: memory/scenario/agent_shell.sh
#   ./play.sh --skip-setup    # venv, cognee and seed already done
#   ./play.sh --keep          # leave the stack running after Ctrl-C
#
# In the agent shell, try: cat ~/.aws/credentials; curl https://1.1.1.1;
# cat /etc/shadow; cp /usr/bin/true /tmp/x. Then rule on a verdict:
#   cd memory && .venv/bin/python -m sentinel review <id> --correct --note "credentials must not leak"
# and read a different secret file to see the learned rule catch it.
. "$(dirname "$0")/stack.sh"
parse_args "$@"
trap teardown EXIT

prerequisites
start_reader
setup_cognee
start_sentinel
[ -f memory/profiles/agent.json ] && step "using the existing agent profile" || learn_from_clean_fleet

say "your turn"
cat <<MSG
   In another terminal:   $PWD/memory/scenario/agent_shell.sh
   Everything you run there is attributed to agent-99, which shares the agent profile.
   Below: what it does, every finding, and the model's verdict when findings appear.
   Review a verdict:      cd memory && .venv/bin/python -m sentinel review <id> --correct|--wrong --note "..."
   Learned rules so far:  cd memory && .venv/bin/python -m sentinel rules
MSG
echo
sentinel tail --watch agent-99 --judge
