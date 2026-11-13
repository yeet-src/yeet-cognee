import argparse
import datetime as dt
import json
import pathlib
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import deny as denylist
from . import rules
from .activity import Activity, brief, summarize
from .ask import verdict
from .llm import describe as describe_llm
from .memory import Memory
from .profile import Finding, Profile
from .reader import Reader

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERDICTS = ROOT / "verdicts.jsonl"
FINDINGS = ROOT / "findings.jsonl"
DEFAULT_WINDOW_S = 90


def now_ms() -> int:
    return int(time.time() * 1000)


def stamp() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def append(path: pathlib.Path, record: dict) -> None:
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def find(path: pathlib.Path, id: str) -> dict | None:
    if not path.exists():
        return None
    return next((r for r in map(json.loads, path.read_text().splitlines()) if r.get("id") == id), None)


class Sentinel:
    def __init__(self, reader: Reader, memory: Memory, host: str, enforce: bool, watch: list[str]):
        self.reader = reader
        self.memory = memory
        self.host = host
        self.enforce = enforce
        self.watch = watch

    def push_deny(self) -> dict:
        sets = denylist.sets()
        result = self.reader.enforce(self.enforce, self.watch, sets)
        if sets.unresolved:
            print(f"[enforce] finding-only (can't be matched by inode or name): {', '.join(sets.unresolved)}", flush=True)
        return {
            "enforcing": self.enforce, "inodes": len(sets.read_inodes), "writes": len(sets.write_inodes),
            "execs": len(sets.exec_inodes), "basenames": len(sets.basenames), "dirs": len(sets.dirs),
            "unresolved": sets.unresolved, **(result or {}),
        }

    def observe(self, watch: list[str], seconds: int) -> list[dict]:
        return self.reader.activity(watch, now_ms() - seconds * 1000)["processes"]

    def findings(self, acts: list[Activity]) -> list[Finding]:
        learned = rules.load()
        return [f for act in acts for f in Profile.load(act.role).findings(act) + _ruled(act, learned)]

    def observe_since(self, watch: list[str], since_ms: int) -> dict:
        payload = self.reader.activity(watch, since_ms)
        acts = [summarize(g) for g in payload["processes"]]
        return {"until": payload["until"], "workloads": {a.name: brief(a) for a in acts}, "findings": [vars(f) for f in self.findings(acts)]}

    def learn(self, watch: list[str], seconds: int) -> list[Profile]:
        profiles: dict[str, Profile] = {}
        for act in map(summarize, self.observe(watch, seconds)):
            profiles.setdefault(act.role, Profile.load(act.role)).learn(act)
        for profile in profiles.values():
            profile.save()
            self.memory.remember(profile.describe(self.host))
        return list(profiles.values())

    def ask(self, question: str, watch: list[str], seconds: int) -> dict:
        groups = self.observe(watch, seconds)
        acts = [summarize(g) for g in groups]
        findings = self.findings(acts)
        text = verdict(question, acts, findings, self.memory.recall(), self.host)
        record = {
            "id": secrets.token_hex(4), "asked_at": stamp(), "question": question, "watch": watch,
            "verdict": text, "findings": [vars(f) for f in findings],
        }
        append(VERDICTS, {**record, "groups": groups})
        return record

    def review(self, id: str, correct: bool, note: str) -> dict:
        record = find(VERDICTS, id)
        if not record:
            raise KeyError(id)
        allowed = [] if correct else self._allow(record)
        rule = rules.compile(id, record["findings"], note, self.memory.recall(_precedent_query(record, note))) if correct else None
        self.memory.remember_later(_review_prose(record, correct, note, allowed, rule), f"review-{id}")
        if self.enforce:
            self.push_deny()
        return {"id": id, "correct": correct, "allowed": sorted(a.name for a in allowed), "rule": rule and vars(rule)}

    def _allow(self, record: dict) -> list[Activity]:
        novel = {f["workload"] for f in record["findings"] if f["kind"] == "novel"}
        acts = [act for act in map(summarize, record["groups"]) if act.name in novel]
        for role in {a.role for a in acts}:
            profile = Profile.load(role)
            for act in acts:
                if act.role == role:
                    profile.allow(act)
            profile.save()
            self.memory.remember_later(profile.describe(self.host), f"profile-{role}")
        return acts

    def patrol(self, watch: list[str], every: int, stop: threading.Event) -> None:
        since = now_ms()
        while not stop.is_set():
            try:
                payload = self.reader.activity(watch, since)
                since = _epoch_ms(payload["until"]) + 1
                acts = [summarize(g) for g in payload["processes"]]
                findings = self.findings(acts)
                if findings:
                    append(FINDINGS, {"at": stamp(), "findings": [vars(f) for f in findings]})
                    self.memory.remember(_observation_prose(findings, self.host))
                print(f"[patrol] {len(acts)} workloads, {len(findings)} findings", flush=True)
            except Exception as exc:
                print(f"[patrol] failed: {exc}", flush=True)
            stop.wait(every)


def _epoch_ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def _ruled(act: Activity, learned: list[rules.Rule]) -> list[Finding]:
    return [
        Finding("policy", act.name, f"opened `{path}`, which matches the learned rule `{', '.join(rule.patterns)}` ({rule.reason})")
        for path in sorted(act.opened)
        for rule in learned
        if rule.matches(path)
    ]


def _review_prose(record: dict, correct: bool, note: str, allowed: list[Activity], rule: rules.Rule | None) -> str:
    outcome = "CONFIRMED" if correct else "REJECTED"
    names = ", ".join(sorted(a.name for a in allowed)) or "these workloads"
    tail = (
        "Treat this behaviour as a real incident when it appears again."
        + (f" It is now a rule: files matching {', '.join(rule.patterns)} are out of bounds because {rule.reason}"
           f" (precedents: {', '.join(rule.precedents)})." if rule else "")
        if correct else
        f"The behaviour of {names} has been added to their profile as allowed."
    )
    return (
        f"# Human review of sentinel verdict {record['id']}\n\n"
        f"On {record['asked_at']} the sentinel was asked about {record['watch'] or 'the whole host'}: "
        f"\"{record['question']}\". It answered: {record['verdict']}\n\n"
        f"A human reviewer {outcome} this verdict" + (f", saying: {note}." if note else ".") + f" {tail}"
    )


def _precedent_query(record: dict, note: str) -> str:
    return "Human reviews of earlier sentinel verdicts about behaviour like this: " + note + " " + " ".join(f["what"] for f in record["findings"])


def _observation_prose(findings: list[Finding], host: str) -> str:
    lines = "\n".join(f"- {f}" for f in findings)
    return f"# Findings on host {host} at {stamp()}\n\nThese were observed and have not been reviewed:\n\n{lines}"


def handler(sentinel: Sentinel):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length)) if length else {}

        def do_GET(self):
            if self.path.rstrip("/") == "/healthz":
                return self._send(200, {"ok": True})
            if self.path.rstrip("/") == "/rules":
                return self._send(200, {"rules": [vars(r) for r in rules.load()]})
            if self.path.rstrip("/") == "/deny":
                s = denylist.sets()
                return self._send(200, {
                    "enforcing": sentinel.enforce, "watch": sentinel.watch,
                    "inodes": [i["path"] for i in s.read_inodes], "writes": [i["path"] for i in s.write_inodes],
                    "execs": [i["path"] for i in s.exec_inodes], "basenames": s.basenames, "dirs": s.dirs,
                    "protectExec": s.protect_exec, "protectPtrace": s.protect_ptrace, "unresolved": s.unresolved,
                })
            self._send(404, {"error": f"no such route: {self.path}"})

        def do_POST(self):
            route = self.path.rstrip("/")
            body = self._body()
            try:
                if route == "/ask":
                    return self._send(200, sentinel.ask(body["question"], body.get("watch") or [], int(body.get("window") or DEFAULT_WINDOW_S)))
                if route == "/learn":
                    profiles = sentinel.learn(body.get("watch") or [], int(body.get("seconds") or DEFAULT_WINDOW_S))
                    return self._send(200, {"profiles": {p.role: sorted(p.workloads) for p in profiles}})
                if route == "/observe":
                    return self._send(200, sentinel.observe_since(body.get("watch") or [], int(body.get("since") or now_ms() - 5000)))
                if route == "/review":
                    return self._send(202, sentinel.review(body["id"], bool(body["correct"]), body.get("note") or ""))
                self._send(404, {"error": f"no such route: {self.path}"})
            except KeyError as exc:
                self._send(400, {"error": f"missing or unknown {exc}"})
            except Exception as exc:
                self._send(500, {"error": str(exc)})

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description="The sentinel memory service: owns cognee, judges, learns, patrols.")
    ap.add_argument("--watch", action="append", default=[], help="prefix of a token of a process's exe, name or argv; repeatable")
    ap.add_argument("--every", type=int, default=60, help="seconds between patrol windows (0 disables the patrol)")
    ap.add_argument("--dataset", default="sysgraph")
    ap.add_argument("--reader", default=None, help="yeet reader URL (default $SENTINEL_READER_URL or http://127.0.0.1:9490)")
    ap.add_argument("--hostname", default=socket.gethostname())
    ap.add_argument("--seed", action="store_true", help="remember context/*.md on startup")
    ap.add_argument("--reset", action="store_true", help="with --seed, forget everything first")
    ap.add_argument("--enforce", action="store_true", help="block denied opens in the kernel, not just flag them")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9495)
    args = ap.parse_args()

    memory = Memory(args.dataset)
    if args.seed:
        print(f"seeded {', '.join(memory.seed(args.reset))}", flush=True)
    sentinel = Sentinel(Reader(args.reader), memory, args.hostname, args.enforce, args.watch)
    pushed = sentinel.push_deny()
    print(
        f"[enforce] {'blocking' if args.enforce else 'observing'}: "
        f"{pushed['inodes']} inodes, {pushed['writes']} tamper, {pushed['execs']} exec, "
        f"{pushed['basenames']} names, {pushed['dirs']} dirs for {args.watch or 'nothing'}",
        flush=True,
    )

    stop = threading.Event()
    if args.every and args.watch:
        threading.Thread(target=sentinel.patrol, args=(args.watch, args.every, stop), name="patrol", daemon=True).start()

    httpd = ThreadingHTTPServer((args.host, args.port), handler(sentinel))
    print(f"sentinel on http://{args.host}:{args.port} (POST /ask /learn /review /observe, GET /rules; verdicts by {describe_llm()})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
