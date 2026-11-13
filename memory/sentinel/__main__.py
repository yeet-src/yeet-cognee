import argparse
import datetime as dt
import json
import os
import sys
import threading
import time
import urllib.request

DEFAULT_URL = "http://127.0.0.1:9495"


def post(url: str, route: str, body: dict) -> dict:
    request = urllib.request.Request(f"{url}{route}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=900) as response:
        return json.loads(response.read())


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
            observed = post(args.server, "/observe", {"watch": args.watch, "since": since})
            since = epoch_ms(observed["until"]) + 1
            report(observed, args)
            time.sleep(args.every)
    except KeyboardInterrupt:
        return 0


def report(observed: dict, args: argparse.Namespace) -> None:
    now = dt.datetime.now().strftime("%H:%M:%S")
    for name, summary in observed["workloads"].items():
        if summary:
            print(f"{now}  {name}: {summary}", flush=True)
    for f in observed["findings"]:
        print(f"{now}  \033[{'31' if f['kind'] == 'policy' else '33'}m[{f['kind']}] {f['workload']}: {f['what']}\033[0m", flush=True)
    if observed["findings"] and args.judge:
        answer = post(args.server, "/ask", {"question": "Is this workload breaking policy or acting outside its profile?", "watch": args.watch, "window": 30})
        print("\n" + answer["verdict"] + f"\n(verdict {answer['id']})\n", flush=True)


HELP = """commands:
  bad [note]     the last verdict was right: remember it and compile a rule from your note
  good [note]    the last verdict was wrong: allow the behaviour in the profile
  ask <question> ask about the watched workloads now
  rules          the rules in force
  quit"""


class Console:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.last: str | None = None
        self.lock = threading.Lock()

    def say(self, text: str) -> None:
        with self.lock:
            sys.stdout.write(f"\r\033[K{text}\nsentinel> ")
            sys.stdout.flush()

    def judge(self) -> None:
        answer = post(self.args.server, "/ask", {"question": "Is this workload breaking policy or acting outside its profile?", "watch": self.args.watch, "window": 30})
        self.last = answer["id"]
        self.say(f"\n{answer['verdict']}\n(verdict {self.last}: type `bad <why>` or `good <why>`)\n")

    def follow(self) -> None:
        since = int(time.time() * 1000)
        while True:
            try:
                observed = post(self.args.server, "/observe", {"watch": self.args.watch, "since": since})
            except OSError:
                time.sleep(self.args.every)
                continue
            since = epoch_ms(observed["until"]) + 1
            now = dt.datetime.now().strftime("%H:%M:%S")
            for name, summary in observed["workloads"].items():
                if summary:
                    self.say(f"{now}  {name}: {summary}")
            for f in observed["findings"]:
                self.say(f"{now}  \033[{'31' if f['kind'] == 'policy' else '33'}m[{f['kind']}] {f['workload']}: {f['what']}\033[0m")
            if observed["findings"]:
                self.judge()
            time.sleep(self.args.every)

    def review(self, correct: bool, note: str) -> None:
        if not self.last:
            return self.say("no verdict yet to rule on")
        result = post(self.args.server, "/review", {"id": self.last, "correct": correct, "note": note})
        if result.get("rule"):
            self.say(f"rule: {', '.join(result['rule']['patterns'])}  ({result['rule']['reason']}; from {', '.join(result['rule']['precedents'])})")
        elif result.get("allowed"):
            self.say(f"allowed for {', '.join(result['allowed'])}: the profile now includes it")
        else:
            self.say("remembered")

    def run(self) -> int:
        threading.Thread(target=self.follow, daemon=True).start()
        self.say(f"following {', '.join(self.args.watch) or 'everything'}. {HELP}")
        try:
            while True:
                line = input().strip()
                verb, _, rest = line.partition(" ")
                if verb in ("quit", "exit", "q"):
                    return 0
                if verb == "bad":
                    self.review(True, rest)
                elif verb == "good":
                    self.review(False, rest)
                elif verb == "ask" and rest:
                    answer = post(self.args.server, "/ask", {"question": rest, "watch": self.args.watch})
                    self.last = answer["id"]
                    self.say(f"\n{answer['verdict']}\n(verdict {self.last})\n")
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

    sub.add_parser("rules", help="list the rules learned from confirmed reviews")

    console = sub.add_parser("console", help="tail plus a prompt: rule on the last verdict as findings come in")
    console.add_argument("--watch", action="append", default=[])
    console.add_argument("--every", type=float, default=2.0)

    args = ap.parse_args()
    if args.command == "tail":
        return follow(args)
    if args.command == "console":
        return Console(args).run()
    if args.command == "rules":
        for r in get(args.server, "/rules")["rules"]:
            print(f"{', '.join(r['patterns'])}  ({r['reason']}; from {', '.join(r['precedents'])})")
        return 0
    if args.command == "ask":
        answer = post(args.server, "/ask", {"question": args.question, "watch": args.watch, "window": args.window})
        print(answer["verdict"])
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
