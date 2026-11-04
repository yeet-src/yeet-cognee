#!/usr/bin/env python3
"""Test fixtures for the sentinel demo: two coding-agent workloads.

These are harmless demo processes. They exist only to leave different,
observable traces in the yeet system graph so the sentinel has something
real to compare:

    baseline   -- works inside its own workspace, opens one network socket
                  to a normal API endpoint, then idles.
    unusual    -- does the same, and additionally holds read-only file
                  descriptors on a few host paths it was told to touch, and
                  opens a socket to a different endpoint.

Neither fixture writes, deletes, or changes anything on the host. The
"unusual" one only *opens* descriptors (O_RDONLY) and then sits still, so
the daemon's kernel view (open fds + sockets) is the only thing that
distinguishes it. The list of paths it touches is passed in explicitly on
the command line -- the fixture has no built-in target list of its own.

    python coder_agent.py --mode baseline  --hold 60
    python coder_agent.py --mode unusual   --hold 60 \
        --touch /etc/hostname --touch /proc/1/status
"""

from __future__ import annotations

import argparse
import os
import pathlib
import socket
import sys
import time

WORKSPACE = pathlib.Path.home() / "agent-workspace"

# A normal API endpoint a coding agent would legitimately reach.
BASELINE_ENDPOINT = ("api.github.com", 443)
# A different endpoint, to show a changed network fingerprint.
UNUSUAL_ENDPOINT = ("1.1.1.1", 443)


def setup_workspace() -> list[int]:
    """Create a small workspace and open a couple of files inside it.

    Returns the open fds so they stay open for the lifetime of the process
    (and therefore stay visible in the sysgraph).
    """
    WORKSPACE.mkdir(exist_ok=True)
    held: list[int] = []
    for name in ("main.py", "notes.md", "data.json"):
        path = WORKSPACE / name
        if not path.exists():
            path.write_text(f"# {name} -- sentinel demo fixture\n")
        held.append(os.open(path, os.O_RDONLY))
    return held


def open_socket(endpoint: tuple[str, int]) -> socket.socket | None:
    """Open a TCP connection and keep it open. Best-effort: if the host has
    no network path to the endpoint, the fixture still runs."""
    try:
        sock = socket.create_connection(endpoint, timeout=5)
        print(f"  connected to {endpoint[0]}:{endpoint[1]}")
        return sock
    except OSError as exc:
        print(f"  could not reach {endpoint[0]}:{endpoint[1]} ({exc}); continuing")
        return None


def touch_readonly(paths: list[str]) -> list[int]:
    """Open each path read-only and hold the descriptor. No writes, no reads
    of content are required -- holding the fd is what the sysgraph records."""
    held: list[int] = []
    for p in paths:
        try:
            fd = os.open(p, os.O_RDONLY)
            held.append(fd)
            print(f"  opened (read-only) {p}")
        except OSError as exc:
            print(f"  could not open {p} ({exc}); skipping")
    return held


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=("baseline", "unusual"), default="baseline")
    ap.add_argument("--hold", type=float, default=60.0,
                    help="seconds to stay alive so the collector can observe it")
    ap.add_argument("--touch", action="append", default=[],
                    help="(unusual mode) a host path to open read-only; repeatable")
    args = ap.parse_args()

    print(f"coder-agent fixture starting in `{args.mode}` mode, pid {os.getpid()}")
    held_fds = setup_workspace()
    held_socks = []

    if args.mode == "baseline":
        s = open_socket(BASELINE_ENDPOINT)
        if s:
            held_socks.append(s)
    else:
        s = open_socket(UNUSUAL_ENDPOINT)
        if s:
            held_socks.append(s)
        if args.touch:
            held_fds += touch_readonly(args.touch)
        else:
            print("  (no --touch paths given; unusual mode differs only by endpoint)")

    print(f"holding for {args.hold:.0f}s so the sentinel collector can observe...")
    try:
        time.sleep(args.hold)
    except KeyboardInterrupt:
        pass
    finally:
        for fd in held_fds:
            os.close(fd)
        for s in held_socks:
            s.close()
    print("coder-agent fixture exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
