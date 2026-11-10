"""A known history of normal, generated for the host being watched.

Memory that learns only from what it sees has a cold-start problem: the
first run it records becomes "normal", even if that run was the bad one.
This module writes the run reports a few days of well-behaved coding agents
would have produced on this host -- the same prose, from the same renderers,
as a live run -- so the first live question is answered against something
solid. The runs are synthetic and say so in their label (`baseline-*`); the
numbers are plausible, not measured.

    python -m sentinel.history            # print them
    python -m sentinel.seed               # ingests them with the policy
"""

from __future__ import annotations

import datetime as dt
import random
import socket

from .report import render_run_report
from .snapshot import Snapshot

WORKSPACE = "/home/jacob/agent-workspace"


def _proc(pid: int, name: str, cwd: str, rss_kb: int, fds: int) -> dict:
    return {
        "pid": pid, "uid": 1001, "exe": "/usr/bin/python3", "cwd": cwd,
        "cmdline": ["python", "scenario/coder_agent.py", "--name", name],
        "fd_count": fds,
        "status": {"name": "python", "ppid": pid - 1, "vmrss": rss_kb, "threads": 1},
        "io": {"read_bytes": 4096 * fds, "write_bytes": 1024 * fds, "syscr": 40 * fds, "syscw": 12 * fds},
        "fds": [{"fd": 3, "kind": "PATH", "path": f"{cwd}/main.py", "inode": 1}],
        "sockets": [],
    }


def _activity(pid: int, name: str, cwd: str, cycles: int, rng: random.Random) -> dict:
    events = []
    for c in range(cycles):
        for f in ("main.py", "notes.md", "data.json"):
            events.append({"kind": "open", "pid": pid, "path": f"{cwd}/{f}", "access": "write", "create": True})
            events.append({"kind": "open", "pid": pid, "path": f"{cwd}/{f}", "access": "read"})
        events.append({"kind": "open", "pid": pid, "path": f"{cwd}/scratch.txt", "access": "write", "create": True})
        events.append({"kind": "exec", "pid": pid + 100 + c, "filename": "/usr/bin/git"})
        events.append({"kind": "connect", "pid": pid, "addr": rng.choice(["140.82.112.5", "140.82.113.6", "140.82.114.5"]), "port": 443})
    return {"pid": pid, "comm": "python", "exe": "/usr/bin/python3", "cwd": cwd,
            "cmdline": ["python", "scenario/coder_agent.py", "--name", name],
            "children": [pid + 100 + c for c in range(cycles)], "events": events, "truncated": 0}


def baseline_runs(hostname: str | None = None, n: int = 6, seed: int = 7) -> list[tuple[str, str]]:
    """`n` run reports of well-behaved agents on `hostname`, spread over the
    past week. Returns (label, prose) pairs."""
    host = hostname or socket.gethostname()
    rng = random.Random(seed)
    now = dt.datetime.now().astimezone()
    runs: list[tuple[str, str]] = []
    for i in range(n):
        label = f"baseline-{i + 1:03d}"
        when = now - dt.timedelta(days=rng.uniform(1, 7))
        agents = rng.sample(range(1, 11), k=rng.randint(2, 4))
        snaps = []
        for s in range(4):
            taken = when + dt.timedelta(seconds=8 * s)
            load = round(rng.uniform(0.2, 1.4), 2)
            snaps.append(Snapshot(
                hostname=host, taken_at=taken, run_label=label,
                system={"load_average": {"one": load, "five": load, "fifteen": load, "cur": 2, "max": 1500},
                        "meminfo": {"mem_total": 64_000_000_000, "mem_available": int(rng.uniform(40e9, 52e9))},
                        "host": {"uptime": {"uptime": 86400 * rng.randint(3, 40)}}},
                watched=[_proc(1000 + a * 10, f"agent-{a:02d}", f"{WORKSPACE}/agent-{a:02d}",
                               rng.randint(28_000, 36_000), rng.randint(5, 9)) for a in agents],
            ))
        activity = {"processes": [_activity(1000 + a * 10, f"agent-{a:02d}", f"{WORKSPACE}/agent-{a:02d}",
                                            rng.randint(2, 4), rng) for a in agents]}
        prose = render_run_report(snaps, activity)
        prose = prose.replace(
            "Everything stated here is measured, not inferred.",
            "It is a reference run of well-behaved agents, recorded as the known-good baseline for this host.", 1)
        runs.append((label, prose))
    return runs


if __name__ == "__main__":
    for label, prose in baseline_runs():
        print(prose)
        print("\n" + "=" * 70 + "\n")
