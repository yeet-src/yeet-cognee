import argparse
import os
import pathlib
import shutil
import socket
import subprocess
import time

WORKSPACES = pathlib.Path.home() / "agent-workspace"
APPROVED_ENDPOINT = ("api.github.com", 443)
UNAPPROVED_ENDPOINT = ("1.1.1.1", 443)
FORBIDDEN_PATHS = ("/var/run/docker.sock", "/etc/shadow")


def work(workspace: pathlib.Path, cycle: int) -> None:
    for name in ("main.py", "notes.md", "data.json"):
        with open(workspace / name, "a") as fh:
            fh.write(f"# cycle {cycle}\n")
        with open(workspace / name) as fh:
            fh.read()
    (workspace / "scratch.txt").write_text(f"cycle {cycle}\n")
    subprocess.run([shutil.which("git") or "/usr/bin/git", "--version"], capture_output=True)
    dial(APPROVED_ENDPOINT)


def dial(endpoint: tuple[str, int]) -> None:
    try:
        socket.create_connection(endpoint, timeout=3).close()
    except OSError:
        pass


def break_policy() -> None:
    for path in FORBIDDEN_PATHS:
        try:
            os.close(os.open(path, os.O_RDONLY))
        except OSError:
            pass
    dial(UNAPPROVED_ENDPOINT)


def peek_at_processes(limit: int = 24) -> None:
    others = (p for p in os.listdir("/proc") if p.isdigit() and int(p) not in (1, os.getpid()))
    for pid in list(others)[:limit]:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                fh.read()
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="A harmless process that leaves the traces of a coding agent.")
    ap.add_argument("--name", default="agent")
    ap.add_argument("--mode", choices=("baseline", "policy", "suspicious"), default="baseline")
    ap.add_argument("--hold", type=float, default=300.0, help="seconds to stay alive")
    ap.add_argument("--cycle", type=float, default=15.0, help="seconds between work cycles")
    args = ap.parse_args()

    workspace = WORKSPACES / args.name
    workspace.mkdir(parents=True, exist_ok=True)
    os.chdir(workspace)
    print(f"{args.name}: {args.mode}, pid {os.getpid()}, workspace {workspace}", flush=True)

    deadline = time.monotonic() + args.hold
    for cycle in range(1, 1_000_000):
        if time.monotonic() >= deadline:
            break
        work(workspace, cycle)
        if args.mode == "policy":
            break_policy()
        elif args.mode == "suspicious":
            peek_at_processes()
        time.sleep(max(0.0, min(args.cycle, deadline - time.monotonic())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
