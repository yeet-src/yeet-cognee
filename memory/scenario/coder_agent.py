#!/usr/bin/env python3
"""Test fixtures for the sentinel demo: a fleet's worth of coding agents.

Each fixture is a harmless process that behaves like a coding agent and
leaves real, observable traces in the kernel: it works in its own workspace,
runs a program, talks to one API endpoint, and then idles. It repeats that
work cycle every few seconds for as long as it is held, so the activity log
always has something recent to say about it. Nothing is held open between
cycles -- a file is opened, read and closed in a millisecond -- which is the
point: a snapshot of open descriptors misses all of it; the activity log
does not.

Three modes, from the two kinds of findings the demo is about:

    baseline     works in its workspace, runs `git --version`, dials
                 api.github.com:443. Well behaved.
    policy       the same, and each cycle also attempts to open the Docker
                 socket and /etc/shadow (the kernel refuses; the attempt is
                 still recorded) and dials 1.1.1.1:443, which the policy
                 does not allow. A clear rule break.
    suspicious   the same as baseline, and also reads the command line of
                 a couple of dozen other processes on the host. No written
                 rule forbids it -- it is just the one agent out of ten
                 that looks at the host instead of its own work.

    python coder_agent.py --name agent-01
    python coder_agent.py --name agent-04 --mode policy
    python coder_agent.py --name agent-09 --mode suspicious
    python coder_agent.py --mode policy --touch /proc/1/environ   # pick your own
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time

WORKSPACE_ROOT = pathlib.Path.home() / "agent-workspace"

BASELINE_ENDPOINT = ("api.github.com", 443)
UNUSUAL_ENDPOINT = ("1.1.1.1", 443)
POLICY_PATHS = ["/var/run/docker.sock", "/etc/shadow"]


def work_cycle(workspace: pathlib.Path, cycle: int) -> None:
    """What every agent does: edit files in its workspace, run a tool, call an API."""
    workspace.mkdir(parents=True, exist_ok=True)
    for name in ("main.py", "notes.md", "data.json"):
        path = workspace / name
        with open(path, "a") as fh:
            fh.write(f"# cycle {cycle}\n")
        with open(path) as fh:
            fh.read()
    (workspace / "scratch.txt").write_text(f"cycle {cycle}\n")

    tool = [shutil.which("git") or "/usr/bin/git", "--version"] if shutil.which("git") else ["uname", "-r"]
    subprocess.run(tool, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    dial(BASELINE_ENDPOINT)


def dial(endpoint: tuple[str, int]) -> None:
    """Connect and close. The attempt is what the kernel records."""
    try:
        socket.create_connection(endpoint, timeout=3).close()
    except OSError:
        pass


def attempt_open(paths: list[str]) -> None:
    """Try to open each path read-only and close it at once. Most of these
    fail for an unprivileged user; the openat() is logged either way."""
    for p in paths:
        try:
            os.close(os.open(p, os.O_RDONLY))
        except OSError:
            pass


def peek_at_processes(limit: int = 24) -> None:
    """Read other processes' command lines: looking at the host, not the work."""
    seen = 0
    for entry in sorted(os.listdir("/proc"), key=lambda e: int(e) if e.isdigit() else 0):
        # pid 1 is the policy's own example of a forbidden path; this agent is
        # meant to be odd, not to break a written rule.
        if not entry.isdigit() or int(entry) in (1, os.getpid()):
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                fh.read()
        except OSError:
            continue
        seen += 1
        if seen >= limit:
            break


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="agent", help="this agent's name; its workspace and how it is watched")
    ap.add_argument("--mode", choices=("baseline", "policy", "suspicious", "unusual"), default="baseline",
                    help="unusual is the old name for policy")
    ap.add_argument("--hold", type=float, default=300.0, help="seconds to stay alive")
    ap.add_argument("--cycle", type=float, default=15.0, help="seconds between work cycles")
    ap.add_argument("--touch", action="append", default=[],
                    help="(policy mode) a host path to attempt instead of the defaults; repeatable")
    args = ap.parse_args()
    mode = "policy" if args.mode == "unusual" else args.mode

    workspace = WORKSPACE_ROOT / args.name
    workspace.mkdir(parents=True, exist_ok=True)
    os.chdir(workspace)  # a coding agent works in its workspace; so does its cwd
    print(f"{args.name}: coder-agent fixture in `{mode}` mode, pid {os.getpid()}, workspace {workspace}", flush=True)

    deadline = time.monotonic() + args.hold
    cycle = 0
    try:
        while time.monotonic() < deadline:
            cycle += 1
            work_cycle(workspace, cycle)
            if mode == "policy":
                attempt_open(args.touch or POLICY_PATHS)
                dial(UNUSUAL_ENDPOINT)
            elif mode == "suspicious":
                peek_at_processes()
            print(f"{args.name}: cycle {cycle} done", flush=True)
            time.sleep(max(0.0, min(args.cycle, deadline - time.monotonic())))
    except KeyboardInterrupt:
        pass
    print(f"{args.name}: exiting", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
