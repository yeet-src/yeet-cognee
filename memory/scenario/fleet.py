import argparse
import pathlib
import signal
import subprocess
import sys
import time

AGENT = pathlib.Path(__file__).resolve().parent / "coder_agent.py"


def roles(count: int, clean: bool) -> dict[str, str]:
    names = [f"agent-{i:02d}" for i in range(1, count + 1)]
    modes = dict.fromkeys(names, "baseline")
    if not clean and count >= 2:
        modes[names[min(3, count - 2)]] = "policy"
        modes[names[-1]] = "suspicious"
    return modes


def main() -> int:
    ap = argparse.ArgumentParser(description="Run a fleet of coding agents: all well behaved with --clean, else one breaks policy and the last one is odd.")
    ap.add_argument("--agents", type=int, default=5)
    ap.add_argument("--hold", type=float, default=180.0, help="seconds each agent runs")
    ap.add_argument("--cycle", type=float, default=15.0, help="seconds between an agent's work cycles")
    ap.add_argument("--clean", action="store_true", help="every agent is well behaved")
    args = ap.parse_args()

    procs = []
    for name, mode in roles(args.agents, args.clean).items():
        cmd = [sys.executable, str(AGENT), "--name", name, "--mode", mode, "--hold", str(args.hold), "--cycle", str(args.cycle)]
        procs.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        print(f"  {name:<9} {mode:<11} pid {procs[-1].pid}", flush=True)
        time.sleep(0.3)

    signal.signal(signal.SIGTERM, lambda *_: [p.terminate() for p in procs])
    print(f"fleet of {len(procs)} running for {args.hold:.0f}s", flush=True)
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
