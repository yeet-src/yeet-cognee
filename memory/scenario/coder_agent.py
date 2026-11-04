#!/usr/bin/env python3
"""Test fixtures for the sentinel demo: two coding-agent workloads.

These are harmless demo processes. They exist only to leave different,
observable traces in the yeet system graph so the sentinel has something
real to compare:

    baseline   -- works inside its own workspace, opens one network socket
                  to a normal API endpoint, then idles.
    unusual    -- does the same, and additionally holds read-only file
                  descriptors on a few host paths (by default /etc/passwd and
                  /proc/1/status), and opens a socket to a different endpoint.

Neither fixture writes, deletes, or changes anything on the host. The
"unusual" one only *opens* descriptors (O_RDONLY) and then sits still, so
the daemon's kernel view (open fds + sockets) is the only thing that
distinguishes it.

    python coder_agent.py --mode baseline
    python coder_agent.py --mode unusual                      # default recon paths
    python coder_agent.py --mode unusual --touch /etc/shadow  # pick your own
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
# Host-revealing paths an agent probing its sandbox would read. Used in unusual
# mode when no --touch is given, so the demo works with no extra flags.
DEFAULT_UNUSUAL_PATHS = ["/etc/passwd", "/proc/1/status"]


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
    ap.add_argument("--hold", type=float, default=300.0,
                    help="seconds to stay alive so it can be observed and asked about")
    ap.add_argument("--touch", action="append", default=[],
                    help="(unusual mode) host path to open read-only; repeatable "
                         f"(default: {' '.join(DEFAULT_UNUSUAL_PATHS)})")
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
        held_fds += touch_readonly(args.touch or DEFAULT_UNUSUAL_PATHS)

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
