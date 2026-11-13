import argparse
import datetime as dt
import json
import os
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


def follow(args: argparse.Namespace) -> int:
    seen: set[str] = set()
    print(f"following {', '.join(args.watch) or 'everything'}; Ctrl-C stops", flush=True)
    try:
        while True:
            seen = report(post(args.server, "/observe", {"watch": args.watch, "seconds": int(args.every) + 1}), seen, args)
            time.sleep(args.every)
    except KeyboardInterrupt:
        return 0


def report(observed: dict, seen: set[str], args: argparse.Namespace) -> set[str]:
    now = dt.datetime.now().strftime("%H:%M:%S")
    for name, summary in observed["workloads"].items():
        if summary:
            print(f"{now}  {name}: {summary}", flush=True)
    fresh = [f for f in observed["findings"] if f"{f['workload']}{f['what']}" not in seen]
    for f in fresh:
        print(f"{now}  \033[{'31' if f['kind'] == 'policy' else '33'}m[{f['kind']}] {f['workload']}: {f['what']}\033[0m", flush=True)
    if fresh and args.judge:
        answer = post(args.server, "/ask", {"question": "Is this workload breaking policy or acting outside its profile?", "watch": args.watch, "window": 30})
        print("\n" + answer["verdict"] + f"\n(verdict {answer['id']})\n", flush=True)
    return {f"{f['workload']}{f['what']}" for f in observed["findings"]}


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

    args = ap.parse_args()
    if args.command == "tail":
        return follow(args)
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
