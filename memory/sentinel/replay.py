"""Replay a recorded activity trace in place of the live yeet reader.

The live demo needs a privileged kernel reader (yeetd) and a running fleet.
This serves the same `/activity` shape from a JSON trace instead, so the whole
sentinel -- findings, the verdict, the case-law loop -- runs on sample data
with no kernel and no fleet. Only the model (for the verdict) is still needed.

A `ReplayReader` is a drop-in for `reader.Reader`: it answers `activity()` from
the trace against a virtual clock so events surface over time, just as the
kernel would feed them, and `enforce()` is a no-op (there is no kernel to arm).
"""

import datetime as dt
import json
import pathlib
import time

from .activity import workload_name


def _iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat()


def _matches(watch: list[str], name: str, cmdline: list[str]) -> bool:
    if not watch:
        return True
    tokens = [name, *cmdline]
    return any(token.startswith(prefix) for prefix in watch for token in tokens)


class ReplayReader:
    """Serve a trace as the yeet reader would, windowed by a virtual clock.

    Each event carries `at`, its seconds offset from the start of the trace.
    With `instant` the whole trace is visible at once (used to learn a baseline
    from it); otherwise an event appears only once that much virtual time, scaled
    by `speed`, has elapsed since this reader was created.
    """

    def __init__(self, trace: str, speed: float = 1.0, instant: bool = False):
        data = json.loads(pathlib.Path(trace).read_text())
        self.workloads = data["workloads"]
        self.speed = speed
        self.instant = instant
        self.start_ms = int(time.time() * 1000)

    def activity(self, watch: list[str], since_ms: int, until_ms: int | None = None, limit: int = 4000) -> dict:
        now = int(time.time() * 1000)
        ceiling = now if until_ms is None else min(until_ms, now)
        processes = []
        for workload in self.workloads:
            cmdline = workload.get("cmdline") or []
            name = workload_name(cmdline, workload.get("comm"))
            if not _matches(watch, name, cmdline):
                continue
            events = [self._strip(e) for e in workload.get("events") or [] if self._visible(e, since_ms, ceiling, now)]
            if events:
                processes.append(self._group(workload, events[:limit]))
        return {"processes": processes, "until": _iso(now if self.instant else ceiling)}

    def enforce(self, on: bool, watch: list[str], sets) -> dict:
        return {"replay": True}

    def _visible(self, event: dict, since_ms: int, ceiling_ms: int, now_ms: int) -> bool:
        if self.instant:
            return True
        at_ms = self.start_ms + int(event.get("at", 0) * 1000 / self.speed)
        return at_ms <= now_ms and since_ms < at_ms <= ceiling_ms

    @staticmethod
    def _strip(event: dict) -> dict:
        return {key: value for key, value in event.items() if key != "at"}

    @staticmethod
    def _group(workload: dict, events: list[dict]) -> dict:
        return {
            "pid": workload["pid"],
            "comm": workload.get("comm"),
            "cmdline": workload.get("cmdline") or [],
            "cwd": workload.get("cwd"),
            "children": workload.get("children") or [],
            "truncated": workload.get("truncated") or 0,
            "events": events,
        }


def build_baseline(trace: str, dataset: str, host: str) -> None:
    """Seed the policy and learn the trace's clean profile, then save a baseline.

    This is the live demo's seed + clean-fleet learn, done against the trace
    instead of a real fleet, so a replay has a baseline without ever needing a
    kernel.
    """
    from . import baseline
    from .memory import Memory
    from .reader import Reader  # noqa: F401 -- kept parallel to serve's construction
    from .serve import Sentinel

    baseline.configure()
    memory = Memory(dataset)
    memory.seed(reset=True)
    sentinel = Sentinel(ReplayReader(trace, instant=True), memory, host, False, ["agent-"])
    sentinel.learn(["agent-"], seconds=10_000)
    print(f"saved baseline -> {baseline.save()}")


def main(argv: list[str] | None = None) -> int:
    import argparse
    import socket

    ap = argparse.ArgumentParser(description="Build a baseline from a recorded trace (no kernel, no fleet).")
    ap.add_argument("action", choices=("build",))
    ap.add_argument("--trace", default=str(pathlib.Path(__file__).resolve().parent.parent / "scenario" / "trace.json"))
    ap.add_argument("--dataset", default="sysgraph")
    ap.add_argument("--hostname", default=socket.gethostname())
    args = ap.parse_args(argv)
    build_baseline(args.trace, args.dataset, args.hostname)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
