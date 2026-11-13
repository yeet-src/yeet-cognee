#!/usr/bin/env bash
# The whole demo in one command: the yeet reader, the sentinel, a clean fleet
# to learn from, a fleet with two bad agents, one verdict, one review.
#
#   ./demo.sh                 # run it all, then tear it down
#   ./demo.sh --keep          # leave the services and the fleet running to ask more
#   ./demo.sh --agents 8      # a bigger fleet
#   ./demo.sh --skip-setup    # venv, cognee and seed already done
#
# Needs: Linux, yeet 0.25+ with yeetd running, python3 (3.10 to 3.14), and the
# LLM + embeddings cognee is pointed at in memory/.env.
. "$(dirname "$0")/stack.sh"
parse_args "$@"
trap teardown EXIT

prerequisites
start_reader
setup_cognee
start_sentinel
learn_from_clean_fleet

say "starting the real fleet (one breaks policy, the last one is odd)"
fleet --hold 3600
step "letting them work for 40s"
sleep 40

say "the verdict"
QUESTION="Across all the agents running now, which ones stand out, and is each a policy violation or just suspicious?"
printf '\n\033[36m$ sentinel ask --watch agent- "%s"\033[0m\n\n' "$QUESTION"
sentinel ask --watch agent- "$QUESTION" | tee "$LOG/verdict.txt"
ID=$(sed -n 's/^(verdict \([0-9a-f]*\);.*/\1/p' "$LOG/verdict.txt")

say "a human confirms it"
printf '\033[36m$ sentinel review %s --correct --note "agent-04 is out of bounds"\033[0m\n' "$ID"
sentinel review "$ID" --correct --note "agent-04 is out of bounds" | sed 's/^/   /'

say "done"
cat <<MSG
   Ask your own question while the fleet runs (--keep):
     cd memory && .venv/bin/python -m sentinel ask --watch agent-05 "what is this agent doing that the others are not?"
   Reject a verdict and the behaviour joins the profile as allowed:
     cd memory && .venv/bin/python -m sentinel review <id> --wrong --note "that endpoint is ours"
   Try it yourself from a shell the sentinel watches:
     ./play.sh
MSG
