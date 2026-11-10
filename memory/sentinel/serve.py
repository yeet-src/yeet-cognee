#!/usr/bin/env python3
"""sentinel.serve -- the memory service. One process owns cognee.

cognee's embedded stores are single-writer: two processes that both open them
(a long-running collector loop AND a separate `ask`) contend for one lock. The
fix is to let ONE process own cognee. This service does that -- it:

  1. runs the collector loop internally: polls the yeet reader on a timer and
     remembers a fresh run report, so memory grows on its own;
  2. records on demand (`POST /remember`), so a scenario can populate memory
     without opening a second cognee client;
  3. answers questions over HTTP (`POST /ask`), reasoning from that same cognee;
  4. takes human reviews of its verdicts (`POST /feedback`) and remembers them,
     so a false alarm corrected once stays corrected.

So it mirrors the yeet side: the reader is a service with routes, and memory is
a service too. `sentinel.ask` becomes a thin client that posts the question
here, which also makes answers fast -- no cognee cold start per question.

    # seed the brain, then loop + serve
    python -m sentinel.serve --seed --reset --watch coder_agent.py --every 5m

    # ask it (from anywhere on the box)
    curl -s localhost:9495/ask -d '{"question":"is this agent escaping?","watch":["coder_agent.py"]}'
    python -m sentinel.ask --watch coder_agent.py "is this agent escaping?"
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import pathlib
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import ask as askmod
from .collector import collect_run, parse_duration
from .llm import describe as describe_llm
from .report import render_run_report
from .yeetq import Yeet

# Every verdict the service gives, one JSON line each, so a human review
# (POST /feedback) can refer back to exactly what was judged and why.
VERDICTS = pathlib.Path(__file__).resolve().parent.parent / "verdicts.jsonl"


class Cognee:
    """Single owner of cognee. All cognee work runs on one persistent event
    loop in one thread, serialized by a lock -- so the store has exactly one
    client and coroutines never land on a different loop than they started on."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._lock = threading.Lock()
        t = threading.Thread(target=self._run, name="cognee", daemon=True)
        t.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def call(self, coro_factory):
        """Run an async cognee op to completion, one at a time."""
        with self._lock:
            fut = asyncio.run_coroutine_threadsafe(coro_factory(), self._loop)
            return fut.result()

    def remember(self, text: str, dataset: str) -> None:
        import cognee
        # add + cognify only: the default self-improvement pass is several more
        # LLM rounds per document, and on a local model it dominates the time.
        self.call(lambda: cognee.remember(text, dataset_name=dataset, self_improvement=False))

    def recall(self, dataset: str) -> str:
        return self.call(lambda: askmod.retrieve_memory(dataset))

    def seed(self, reset: bool, dataset: str, history: bool = True) -> None:
        from .seed import seed
        self.call(lambda: seed(reset, dataset, history))


def collector_loop(cog: Cognee, args: argparse.Namespace, stop: threading.Event) -> None:
    """Poll the reader and remember, forever, until asked to stop."""
    every = parse_duration(args.every)
    cycle = 0
    yeet = Yeet(url=args.url)
    print(f"[loop] a run of {args.samples} readings every {args.every}", flush=True)
    while not stop.is_set():
        cycle += 1
        label = f"{args.label}-{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}"
        try:
            snaps, activity = collect_run(yeet, args.watch, label, args.samples, args.interval, args.hostname)
            if snaps:
                cog.remember(render_run_report(snaps, activity), args.dataset)
                print(f"[loop] cycle {cycle}: remembered {label}", flush=True)
            else:
                print(f"[loop] cycle {cycle}: no readings; will retry", file=sys.stderr, flush=True)
        except Exception as exc:  # a bad cycle must not kill the loop
            print(f"[loop] cycle {cycle} failed: {exc}", file=sys.stderr, flush=True)
        stop.wait(every)


def make_handler(cog: Cognee, args: argparse.Namespace):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the console for the loop, not access logs
            pass

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/") == "/healthz":
                return self._send(200, {"ok": True})
            self._send(404, {"error": f"no such route: {self.path}"})

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

        def do_POST(self):
            route = self.path.rstrip("/")
            try:
                if route == "/ask":
                    return self._ask(self._body())
                if route == "/remember":
                    return self._remember(self._body())
                if route == "/feedback":
                    return self._feedback(self._body())
                self._send(404, {"error": f"no such route: {self.path}"})
            except Exception as exc:
                self._send(500, {"error": str(exc)})

        def _ask(self, body: dict):
            question = (body.get("question") or "").strip()
            if not question:
                return self._send(400, {"error": "missing 'question'"})
            watch = body.get("watch") or []
            window = int(body.get("window") or askmod.DEFAULT_WINDOW_S)
            # live reading + verdict are stateless (HTTP to reader / the LLM);
            # only the recall touches cognee, through the single owner.
            live = askmod.gather_live(Yeet(url=args.url), watch, window)
            memory = cog.recall(args.dataset)
            text = askmod.verdict(question, live, memory)
            record = {
                "id": secrets.token_hex(4),
                "asked_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "question": question, "watch": watch, "verdict": text,
                "verdict_line": next((l for l in text.splitlines() if l.startswith("VERDICT:")), ""),
            }
            with VERDICTS.open("a") as fh:
                fh.write(json.dumps({**record, "live": live}) + "\n")
            self._send(200, record)

        def _feedback(self, body: dict):
            """A human says a verdict was right or wrong. The review is remembered
            as prose that names the verdict, the question, the evidence and the
            correction, so recall surfaces it next time something similar is asked."""
            vid = str(body.get("id") or "").strip()
            if not vid or "correct" not in body:
                return self._send(400, {"error": "need 'id' and 'correct' (true/false)"})
            record = next((json.loads(l) for l in VERDICTS.read_text().splitlines()
                           if json.loads(l).get("id") == vid), None) if VERDICTS.exists() else None
            if not record:
                return self._send(404, {"error": f"no verdict {vid}"})
            correct = bool(body["correct"])
            note = (body.get("note") or "").strip()
            review = (
                f"# Human review of sentinel verdict {vid}\n\n"
                f"On {record['asked_at']}, the sentinel was asked about the workloads matching "
                f"{record['watch'] or 'the whole host'}: \"{record['question']}\". "
                f"It answered: {record['verdict']}\n\n"
                f"A human reviewer {'CONFIRMED' if correct else 'REJECTED'} this verdict"
                + (f", saying: {note}" if note else ".")
                + ("\n\nTreat behaviour like this as a real incident when it appears again."
                   if correct else
                   "\n\nThe verdict was wrong. A future verdict about the same behaviour on this "
                   "host should follow the reviewer's correction, not the earlier answer.")
            )
            # The verdict already cites its evidence, so the review stays short
            # and cognifies in seconds; it is remembered in the background and
            # the caller is not kept waiting on the model.
            threading.Thread(target=cog.remember, args=(review, args.dataset),
                             name=f"feedback-{vid}", daemon=True).start()
            self._send(202, {"id": vid, "accepted": True, "correct": correct})

        def _remember(self, body: dict):
            # Record one run now, through the owned cognee -- so a scenario can
            # populate memory without opening a second cognee client.
            watch = body.get("watch") or args.watch
            samples = int(body.get("samples") or args.samples)
            interval = float(body.get("interval") or args.interval)
            lead = int(body.get("lead") or 30)
            label = f"{body.get('label') or args.label}-{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}"
            snaps, activity = collect_run(Yeet(url=args.url), watch, label, samples, interval,
                                          args.hostname, lead_ms=lead * 1000)
            if not snaps:
                return self._send(200, {"remembered": None, "readings": 0})
            cog.remember(render_run_report(snaps, activity), args.dataset)
            self._send(200, {"remembered": label, "readings": len(snaps),
                             "actions": (activity or {}).get("events", 0)})

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--watch", action="append", default=[],
                    help="substring matched against a process's exe and cmdline; repeatable")
    ap.add_argument("--label", default="watch", help="base run label (a timestamp is appended)")
    ap.add_argument("--samples", type=int, default=4, help="readings per run (default 4)")
    ap.add_argument("--interval", type=float, default=8.0, help="seconds between readings")
    ap.add_argument("--every", default="5m", help="time between runs: 30s, 5m, 1h, or seconds")
    ap.add_argument("--dataset", default="sysgraph", help="cognee dataset name")
    ap.add_argument("--url", default=None,
                    help="sysgraph reader URL (default $SENTINEL_READING_URL or http://127.0.0.1:9490/reading)")
    ap.add_argument("--hostname", default=None, help="override the recorded host name")
    ap.add_argument("--seed", action="store_true", help="ingest context/*.md (policy + runbook) on startup")
    ap.add_argument("--reset", action="store_true", help="with --seed, wipe all memory first")
    ap.add_argument("--no-history", action="store_true",
                    help="with --seed, skip the known-good history (history/*.md)")
    ap.add_argument("--no-loop", action="store_true", help="serve /ask only; do not record on a timer")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=9495, help="bind port (default 9495)")
    args = ap.parse_args()

    cog = Cognee()

    if args.seed:
        print("Seeding policy, runbook" + ("" if args.no_history else " and known-good history")
              + " into cognee...", flush=True)
        cog.seed(args.reset, args.dataset, history=not args.no_history)

    stop = threading.Event()
    if not args.no_loop:
        threading.Thread(target=collector_loop, args=(cog, args, stop),
                         name="collector", daemon=True).start()

    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(cog, args))
    print(f"sentinel memory service on http://{args.host}:{args.port}  "
          f"(POST /ask /remember /feedback, GET /healthz; verdicts by {describe_llm()})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...", flush=True)
    finally:
        stop.set()
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
