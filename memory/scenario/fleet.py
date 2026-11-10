#!/usr/bin/env python3
"""Run a fleet of coding agents on this host: most well behaved, a few not.

Starts N copies of coder_agent.py, each with its own name and workspace. By
default agent-04 breaks the written policy and agent-09 is merely odd; every
other agent is baseline. All of them are watched by the same yeet reader and
remembered in the same cognee, which is what lets the sentinel sort the odd
ones out: by the policy for one, by comparison with its peers for the other.

    python scenario/fleet.py                       # 10 agents for 3 minutes
    python scenario/fleet.py --agents 5 --hold 90
    python scenario/fleet.py --policy agent-02 --suspicious agent-05
"""

from __future__ import annotations

import argparse
import pathlib
import signal
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agents", type=int, default=10)
    ap.add_argument("--hold", type=float, default=180.0, help="seconds each agent runs")
    ap.add_argument("--cycle", type=float, default=15.0, help="seconds between an agent's work cycles")
    ap.add_argument("--policy", action="append", default=None, help="agent name(s) that break policy (default agent-04)")
    ap.add_argument("--suspicious", action="append", default=None, help="agent name(s) that act oddly (default agent-09)")
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    names = [f"agent-{i:02d}" for i in range(1, args.agents + 1)]
    policy = set(args.policy if args.policy is not None else (["agent-04"] if args.agents >= 4 else []))
    suspicious = set(args.suspicious if args.suspicious is not None else (["agent-09"] if args.agents >= 9 else [names[-1]] if args.agents >= 2 else []))

    procs: list[subprocess.Popen] = []
    for name in names:
        mode = "policy" if name in policy else "suspicious" if name in suspicious else "baseline"
        cmd = [args.python, str(HERE / "coder_agent.py"), "--name", name, "--mode", mode,
               "--hold", str(args.hold), "--cycle", str(args.cycle)]
        procs.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        print(f"  {name:<9} {mode:<11} pid {procs[-1].pid}", flush=True)
        time.sleep(0.3)  # stagger so the cycles don't line up exactly

    def stop(*_):
        for p in procs:
            p.terminate()
    signal.signal(signal.SIGTERM, stop)
    print(f"fleet of {len(procs)} running for {args.hold:.0f}s; Ctrl-C stops it", flush=True)
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
