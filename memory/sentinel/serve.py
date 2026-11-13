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
from .activity import Activity, brief, role_of, summarize
from .ask import needs_decision, verdict
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

    def observe_since(self, watch: list[str], since_ms: int, verbose: bool = False) -> dict:
        payload = self.reader.activity(watch, since_ms)
        acts = [summarize(g) for g in payload["processes"]]
        out = {"until": payload["until"], "workloads": {a.name: brief(a) for a in acts}, "findings": [vars(f) for f in self.findings(acts)]}
        if verbose:
            out["events"] = [e for act, group in zip(acts, payload["processes"]) for e in _events(act.name, group)][:100]
        return out

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
            "verdict": text, "decision": needs_decision(text), "findings": [vars(f) for f in findings],
        }
        append(VERDICTS, {**record, "groups": groups})
        return record

    def add_rule(self, patterns: list[str], reason: str, action: str) -> rules.Rule | None:
        patterns = [p.strip() for p in patterns if p.strip()]
        if not patterns:
            return None
        existing = rules.load()
        rule = rules.Rule(tuple(patterns), reason or "set by a human", (f"manual-{secrets.token_hex(3)}",), action if action in ("read", "write", "exec") else "read")
        rules.save([*existing, rule])
        self.memory.remember_later(_rule_prose(rule, self.host), f"rule-{rule.verdict}")
        if self.enforce:
            self.push_deny()
        return rule

    def review(self, id: str, correct: bool, note: str, which: list[int] | None = None) -> dict:
        record = find(VERDICTS, id)
        if not record:
            raise KeyError(id)
        findings = record["findings"]
        chosen = [findings[i] for i in which if 0 <= i < len(findings)] if which else findings
        rule = rules.compile(id, chosen, note, self.memory.recall(_precedent_query(chosen, note))) if correct else None
        allowed = [] if correct else self._bless(chosen)
        self.memory.remember_later(_review_prose(record, correct, note, allowed, rule, chosen), f"review-{id}")
        if self.enforce:
            self.push_deny()
        return {"id": id, "correct": correct, "allowed": allowed, "rule": rule and vars(rule), "count": len(chosen)}

    def _bless(self, findings: list[dict]) -> list[str]:
        touched: dict[str, Profile] = {}
        blessed: list[str] = []
        for f in findings:
            profile = touched.setdefault(role_of(f["workload"]), Profile.load(role_of(f["workload"])))
            if profile.bless(f.get("category", ""), f.get("subject", "")):
                blessed.append(f.get("subject") or f["what"])
        for role, profile in touched.items():
            profile.save()
            self.memory.remember_later(profile.describe(self.host), f"profile-{role}")
        return sorted(set(blessed))

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


def _events(name: str, group: dict) -> list[dict]:
    rows = []
    for e in group.get("events", []):
        kind = e.get("kind")
        if kind == "open":
            rows.append({"workload": name, "verb": e.get("access", "read"), "what": e.get("path"), "blocked": bool(e.get("blocked"))})
        elif kind == "exec":
            rows.append({"workload": name, "verb": "exec", "what": e.get("filename"), "blocked": False})
        elif kind == "connect":
            rows.append({"workload": name, "verb": "conn", "what": f"{e.get('addr')}:{e.get('port')}", "blocked": False})
    return rows


def _ruled(act: Activity, learned: list[rules.Rule]) -> list[Finding]:
    return [
        Finding("policy", act.name, f"opened `{path}`, which matches the learned rule `{', '.join(rule.patterns)}` ({rule.reason})", "rule", path)
        for path in sorted(act.opened)
        for rule in learned
        if rule.matches(path)
    ]


def _review_prose(record: dict, correct: bool, note: str, allowed: list[str], rule: rules.Rule | None, chosen: list[dict]) -> str:
    outcome = "CONFIRMED" if correct else "REJECTED"
    items = "; ".join(f["what"] for f in chosen) or "the whole verdict"
    tail = (
        "Treat this behaviour as a real incident when it appears again."
        + (f" It is now a rule: files matching {', '.join(rule.patterns)} are out of bounds because {rule.reason}"
           f" (precedents: {', '.join(rule.precedents)})." if rule else "")
        if correct else
        f"These are allowed now and were added to the profile: {', '.join(allowed) or 'nothing to add'}."
    )
    return (
        f"# Human review of sentinel verdict {record['id']}\n\n"
        f"On {record['asked_at']} the sentinel was asked about {record['watch'] or 'the whole host'}: "
        f"\"{record['question']}\". It answered: {record['verdict']}\n\n"
        f"A human reviewer {outcome} these findings: {items}" + (f", saying: {note}." if note else ".") + f" {tail}"
    )


def _precedent_query(findings: list[dict], note: str) -> str:
    return "Human reviews of earlier sentinel verdicts about behaviour like this: " + note + " " + " ".join(f["what"] for f in findings)


def _rule_prose(rule: rules.Rule, host: str) -> str:
    verb = {"write": "written to", "exec": "run"}.get(rule.action, "read")
    return (
        f"# Human-set rule on host {host} at {stamp()}\n\n"
        f"A human declared that files matching {', '.join(rule.patterns)} must not be {verb} by a watched workload, "
        f"because {rule.reason}. This was set directly, not from a verdict."
    )


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
                    return self._send(200, sentinel.observe_since(body.get("watch") or [], int(body.get("since") or now_ms() - 5000), bool(body.get("verbose"))))
                if route == "/review":
                    return self._send(202, sentinel.review(body["id"], bool(body["correct"]), body.get("note") or "", body.get("which")))
                if route == "/rule":
                    patterns = body.get("patterns") or ([body["pattern"]] if body.get("pattern") else [])
                    rule = sentinel.add_rule(patterns, body.get("reason") or "", body.get("action") or "read")
                    return self._send(201, {"rule": rule and vars(rule)})
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
