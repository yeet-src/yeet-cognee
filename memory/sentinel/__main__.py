import argparse
import datetime as dt
import json
import os
import sys
import threading
import time
import urllib.request

from . import pretty
from .review_tui import Review

DEFAULT_URL = "http://127.0.0.1:9495"


def post(url: str, route: str, body: dict) -> dict:
    request = urllib.request.Request(f"{url}{route}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=900) as response:
        return json.loads(response.read())


def deny_pattern(server: str, pattern: str, reason: str) -> dict:
    return post(server, "/rule", {"pattern": os.path.expanduser(pattern), "reason": reason, "action": "read"})


def submit_decision(server: str, decision: dict) -> dict:
    result = {}
    if decision["bad"]:
        result["rule"] = post(server, "/review", {"id": decision["id"], "correct": True, "note": decision["reason"], "which": decision["bad"]}).get("rule")
    if decision["good"]:
        result["allowed"] = post(server, "/review", {"id": decision["id"], "correct": False, "note": "", "which": decision["good"]}).get("allowed")
    return result


def decision_summary(result: dict) -> str:
    lines = []
    if result.get("rule"):
        r = result["rule"]
        lines.append(f"rule: {', '.join(r['patterns'])}  ({r['reason']}; from {', '.join(r['precedents'])})")
    if result.get("allowed"):
        lines.append(f"added to the profile as normal: {', '.join(result['allowed'])}")
    return "\n".join(lines) or "nothing marked"


def get(url: str, route: str) -> dict:
    with urllib.request.urlopen(f"{url}{route}", timeout=30) as response:
        return json.loads(response.read())


def epoch_ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def follow(args: argparse.Namespace) -> int:
    since = int(time.time() * 1000)
    print(f"following {', '.join(args.watch) or 'everything'}; Ctrl-C stops", flush=True)
    try:
        while True:
            observed = post(args.server, "/observe", {"watch": args.watch, "since": since, "verbose": args.verbose})
            since = epoch_ms(observed["until"]) + 1
            report(observed, args)
            time.sleep(args.every)
    except KeyboardInterrupt:
        return 0


def report(observed: dict, args: argparse.Namespace) -> None:
    if args.verbose:
        for ev in observed.get("events", []):
            print(pretty.event_line(ev), flush=True)
    else:
        for name, summary in observed["workloads"].items():
            if summary:
                print(pretty.workload_line(name, summary), flush=True)
    for f in observed["findings"]:
        print(pretty.finding_line(f), flush=True)
    if observed["findings"] and args.judge:
        answer = post(args.server, "/ask", {"question": "Does anything here need a human decision?", "watch": args.watch, "window": 30})
        print(f"\n{pretty.decision_banner(answer.get('decision'))}  {answer['verdict']}\n(verdict {answer['id']})\n", flush=True)


HELP = """when something is flagged, you decide (# = the number beside a finding; omit # to mean all):
  bad <#> <why>      it's an incident → becomes a standing rule       e.g.  bad 1 the docker socket is off limits
  good <#>           it's fine        → added to the agent's profile   e.g.  good 2 3
  review             a panel to toggle good/bad per finding and add a reason

  ask <question>     ask about the watched agents right now
  deny <path> [why]  forbid a path immediately, no verdict needed      e.g.  deny ~/secret.txt personal
  rules              show the rules in force
  verbose            show every file open, exec and connect (off by default)
  quit"""


def split_ids(rest: str) -> tuple[list[int], str]:
    tokens = rest.split()
    ids: list[int] = []
    while tokens and all(c.isdigit() or c == "," for c in tokens[0]):
        ids += [int(x) for x in tokens.pop(0).split(",") if x]
    return ids, " ".join(tokens)


class Console:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.last: str | None = None
        self.verdict = ""
        self.findings: list[dict] = []
        self.lock = threading.Lock()
        self.paused = threading.Event()
        self.judging = threading.Event()
        self.verbose = getattr(args, "verbose", False)

    def say(self, text: str) -> None:
        with self.lock:
            sys.stdout.write(f"\r\033[K{text}\nsentinel> ")
            sys.stdout.flush()

    def show_verdict(self, answer: dict) -> None:
        self.last = answer["id"]
        self.verdict = answer.get("verdict", "")
        self.findings = answer.get("findings", [])
        needs = answer.get("decision")
        lines = [f"\n{pretty.decision_banner(needs)}  {self.verdict}"]
        if needs:
            lines.append("")
            lines += [pretty.numbered_finding(i, f) for i, f in enumerate(self.findings, 1)]
            lines.append(f"{pretty.GREY}→ your call:  bad <#> <why>   (e.g. bad 1 the docker socket is off limits)   •   good <#>  if it's fine   •   review  for a panel{pretty.RESET}")
        self.say("\n".join(lines))

    def open_panel(self) -> None:
        if not self.last:
            return self.say("no verdict yet to rule on")
        self.paused.set()
        try:
            decision = Review({"id": self.last, "verdict": self.verdict, "findings": self.findings}).run()
        finally:
            self.paused.clear()
        if not decision:
            return self.say("cancelled")
        if decision["bad"]:
            self.say("compiling a rule…")
        self.say(decision_summary(submit_decision(self.args.server, decision)))

    def judge(self) -> None:
        try:
            self.say(f"{pretty.GREY}judging…{pretty.RESET}")
            self.show_verdict(post(self.args.server, "/ask", {"question": "Does anything here need a human decision?", "watch": self.args.watch, "window": 30}))
        finally:
            self.judging.clear()

    def follow(self) -> None:
        since = int(time.time() * 1000)
        while True:
            if self.paused.is_set():
                time.sleep(self.args.every)
                continue
            try:
                observed = post(self.args.server, "/observe", {"watch": self.args.watch, "since": since, "verbose": self.verbose})
            except OSError:
                time.sleep(self.args.every)
                continue
            since = epoch_ms(observed["until"]) + 1
            if self.verbose:
                for ev in observed.get("events", []):
                    self.say(pretty.event_line(ev))
            else:
                for name, summary in observed["workloads"].items():
                    if summary:
                        self.say(pretty.workload_line(name, summary))
            for f in observed["findings"]:
                self.say(pretty.finding_line(f))
            if observed["findings"] and not self.judging.is_set():
                self.judging.set()
                threading.Thread(target=self.judge, daemon=True).start()
            time.sleep(self.args.every)

    def review(self, correct: bool, rest: str) -> None:
        if not self.last:
            return self.say("no verdict yet to rule on")
        ids, note = split_ids(rest)
        which = [i - 1 for i in ids] or None
        result = post(self.args.server, "/review", {"id": self.last, "correct": correct, "note": note, "which": which})
        if result.get("rule"):
            self.say(f"rule: {', '.join(result['rule']['patterns'])}  ({result['rule']['reason']}; from {', '.join(result['rule']['precedents'])})")
        elif result.get("allowed"):
            self.say(f"added to the profile as normal: {', '.join(result['allowed'])}")
        else:
            self.say("remembered")

    def run(self) -> int:
        threading.Thread(target=self.follow, daemon=True).start()
        watching = ", ".join(self.args.watch) or "everything"
        intro = f"Watching {watching}. I summarise what each one does, flag anything off, and ask you to decide."
        self.say(f"{pretty.BOLD}{intro}{pretty.RESET}\n\n{HELP}")
        try:
            while True:
                line = input().strip()
                verb, _, rest = line.partition(" ")
                if verb in ("quit", "exit", "q"):
                    return 0
                if verb in ("verbose", "v"):
                    self.verbose = not self.verbose
                    self.say(f"{pretty.GREY}verbose {'on — every file open, exec and connect' if self.verbose else 'off — summaries and findings only'}{pretty.RESET}")
                elif verb in ("review", "r"):
                    self.open_panel()
                elif verb == "deny" and rest:
                    pattern, _, reason = rest.partition(" ")
                    result = deny_pattern(self.args.server, pattern, reason)
                    rule = result.get("rule")
                    self.say(f"rule: {', '.join(rule['patterns'])}  ({rule['reason']})" if rule else "nothing to deny")
                elif verb == "bad":
                    self.review(True, rest)
                elif verb == "good":
                    self.review(False, rest)
                elif verb == "ask" and rest:
                    self.show_verdict(post(self.args.server, "/ask", {"question": rest, "watch": self.args.watch}))
                elif verb == "rules":
                    rules = get(self.args.server, "/rules")["rules"]
                    self.say("\n".join(f"{', '.join(r['patterns'])}  ({r['reason']}; from {', '.join(r['precedents'])})" for r in rules) or "no rules yet")
                elif verb:
                    self.say(HELP)
                else:
                    self.say("")
        except (EOFError, KeyboardInterrupt):
            return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="sentinel", description="Talk to the running sentinel service.")
    ap.add_argument("--server", default=os.environ.get("SENTINEL_SERVER_URL") or DEFAULT_URL)
    sub = ap.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="judge the watched workloads now")
    ask.add_argument("question")
    ask.add_argument("--watch", action="append", default=[])
    ask.add_argument("--window", type=int, default=90, help="seconds of activity to judge")

    learn = sub.add_parser("learn", help="fold the last N seconds into the watched workloads' profiles")
    learn.add_argument("--watch", action="append", default=[])
    learn.add_argument("--seconds", type=int, default=90)

    review = sub.add_parser("review", help="confirm or reject a verdict by id")
    review.add_argument("id")
    group = review.add_mutually_exclusive_group(required=True)
    group.add_argument("--correct", action="store_true")
    group.add_argument("--wrong", action="store_true")
    review.add_argument("--note", default="")

    tail = sub.add_parser("tail", help="print what the watched workloads do and how the rules feel about it, live")
    tail.add_argument("--watch", action="append", default=[])
    tail.add_argument("--every", type=float, default=2.0)
    tail.add_argument("--judge", action="store_true", help="also ask the model whenever findings appear")
    tail.add_argument("--verbose", action="store_true", help="every file open, exec and connect, not just summaries")

    sub.add_parser("rules", help="list the rules learned from confirmed reviews")

    console = sub.add_parser("console", help="tail plus a prompt: rule on the last verdict as findings come in")
    console.add_argument("--watch", action="append", default=[])
    console.add_argument("--every", type=float, default=1.0)
    console.add_argument("--verbose", action="store_true", help="start with every open/exec/connect shown (toggle with `verbose`)")

    judge = sub.add_parser("judge", help="ask, then toggle good/bad on each finding in a panel")
    judge.add_argument("--watch", action="append", default=[])
    judge.add_argument("--question", default="Is this workload breaking policy or acting outside its profile?")
    judge.add_argument("--window", type=int, default=90)

    deny = sub.add_parser("deny", help="forbid a path directly, without a verdict")
    deny.add_argument("pattern", help="an exact path (~ is expanded) or a glob like '*/secrets/*'")
    deny.add_argument("reason", nargs="*", help="why, in your words")
    deny.add_argument("--action", choices=("read", "write", "exec"), default="read")

    args = ap.parse_args()
    if args.command == "tail":
        return follow(args)
    if args.command == "console":
        return Console(args).run()
    if args.command == "judge":
        answer = post(args.server, "/ask", {"question": args.question, "watch": args.watch, "window": args.window})
        decision = Review(answer).run()
        if not decision:
            print("cancelled")
            return 0
        if decision["bad"]:
            print("compiling a rule from the flagged findings…")
        print(decision_summary(submit_decision(args.server, decision)))
        return 0
    if args.command == "deny":
        pattern = os.path.expanduser(args.pattern)
        result = post(args.server, "/rule", {"pattern": pattern, "reason": " ".join(args.reason), "action": args.action})
        rule = result.get("rule")
        print(f"rule: {', '.join(rule['patterns'])}  ({rule['reason']})" if rule else "nothing to deny")
        return 0
    if args.command == "rules":
        for r in get(args.server, "/rules")["rules"]:
            print(f"{', '.join(r['patterns'])}  ({r['reason']}; from {', '.join(r['precedents'])})")
        return 0
    if args.command == "ask":
        answer = post(args.server, "/ask", {"question": args.question, "watch": args.watch, "window": args.window})
        print(f"{pretty.decision_banner(answer.get('decision'))}  {answer['verdict']}")
        print(f"\n(verdict {answer['id']}; `python -m sentinel review {answer['id']} --correct|--wrong --note '...'`)")
    elif args.command == "learn":
        result = post(args.server, "/learn", {"watch": args.watch, "seconds": args.seconds})
        for role, workloads in result["profiles"].items():
            print(f"profile `{role}` learned from {', '.join(workloads)}")
    else:
        result = post(args.server, "/review", {"id": args.id, "correct": args.correct, "note": args.note})
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
