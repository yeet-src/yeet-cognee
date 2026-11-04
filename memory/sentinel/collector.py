"""Collector: read yeet a few times, render prose, hand it to cognee.

This records ONE run and exits -- several readings folded into one prose run
report, remembered by cognee. It is the right tool for scenarios and for seeding
history. For an always-on feed that keeps memory growing while questions are
being asked, use `sentinel.serve`, which owns cognee and runs this same loop
internally (so there is never a second cognee client).

    python -m sentinel.collector --watch coder_agent.py --label benign-001
    python -m sentinel.collector --watch coder_agent.py --label x --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
import time

from .report import render_run_report
from .snapshot import Snapshot, render, take
from .yeetq import Yeet, YeetError

SNAPSHOT_DIR = pathlib.Path(__file__).resolve().parent.parent / "snapshots"


def parse_duration(spec: str) -> float:
    """Seconds from a string like '30s', '5m', '1h', or a bare number."""
    s = str(spec).strip().lower()
    units = {"s": 1, "m": 60, "h": 3600}
    if s and s[-1] in units:
        return float(s[:-1]) * units[s[-1]]
    return float(s)


def collect(
    yeet: Yeet,
    watch: list[str],
    label: str,
    samples: int,
    interval: float,
    hostname: str | None,
) -> list[Snapshot]:
    snaps: list[Snapshot] = []
    for i in range(samples):
        if i:
            time.sleep(interval)
        try:
            snap = take(yeet, watch=watch, run_label=label, hostname=hostname)
        except YeetError as exc:
            print(f"  [{i + 1}/{samples}] yeet error: {exc}", file=sys.stderr)
            continue
        snaps.append(snap)
        found = ", ".join(
            f"{(p.get('status') or {}).get('name')}(pid {p['pid']})" for p in snap.watched
        ) or "nothing watched running"
        print(f"  [{i + 1}/{samples}] {snap.stamp}  {found}")
    return snaps


async def ingest(text: str, dataset: str) -> None:
    import cognee

    print(f"\nHanding {len(text)} characters of prose to cognee (dataset `{dataset}`)...")
    started = time.monotonic()
    await cognee.remember(text, dataset_name=dataset)
    print(f"cognee remembered it in {time.monotonic() - started:.1f}s")


def record_once(yeet: Yeet, args: argparse.Namespace, label: str) -> bool:
    """Record one run (several readings → one or more prose docs) into cognee.

    Returns True if something was recorded, False if no readings were taken
    (so a loop can log it and carry on rather than die)."""
    print(f"Collecting run `{label}` ({args.samples} readings, {args.interval}s apart)")
    snaps = collect(yeet, args.watch, label, args.samples, args.interval, args.hostname)
    if not snaps:
        print("No readings taken.", file=sys.stderr)
        return False

    SNAPSHOT_DIR.mkdir(exist_ok=True)
    docs = (
        [render(s) for s in snaps] if args.per_snapshot
        else [render_run_report(snaps)]
    )

    for n, doc in enumerate(docs):
        suffix = f".{n}" if len(docs) > 1 else ""
        out = SNAPSHOT_DIR / f"{label}{suffix}.md"
        out.write_text(doc)
        print(f"Wrote {out}")

    if args.dry_run:
        print("\n" + "=" * 70)
        for doc in docs:
            print(doc)
        return True

    for doc in docs:
        asyncio.run(ingest(doc, args.dataset))
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--watch", action="append", default=[],
                    help="substring matched against a process's exe and cmdline; repeatable")
    ap.add_argument("--label", default="run", help="run label, e.g. benign-001")
    ap.add_argument("--samples", type=int, default=4, help="readings per run (default 4)")
    ap.add_argument("--interval", type=float, default=8.0, help="seconds between readings")
    ap.add_argument("--dataset", default="sysgraph", help="cognee dataset name")
    ap.add_argument("--url", default=None,
                    help="sysgraph service URL (default $SENTINEL_READING_URL or http://127.0.0.1:9490/reading)")
    ap.add_argument("--hostname", default=None, help="override the recorded host name")
    ap.add_argument("--dry-run", action="store_true", help="print the prose, do not ingest")
    ap.add_argument("--per-snapshot", action="store_true",
                    help="remember each reading separately instead of one run report")
    args = ap.parse_args()

    yeet = Yeet(url=args.url)
    return 0 if record_once(yeet, args, args.label) else 1


if __name__ == "__main__":
    raise SystemExit(main())
