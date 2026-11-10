#!/usr/bin/env python3
"""Seed the sentinel's memory with the company brain.

This ingests the organisation's own knowledge about the host -- the sandbox
policy and the operations runbook -- into cognee. This is the half of the
demo that lets the sentinel answer "is this normal?" with reference to how
*this* organisation runs *this* box, not generic knowledge.

Run this once before the scenarios. Pair it with the sysgraph run reports the
collector produces, and cognee links the two: a live reading can then be
judged against both the written policy and the host's own history.

    python -m sentinel.seed
    python -m sentinel.seed --reset         # wipe all memory first
    python -m sentinel.seed --no-history    # policy + runbook only
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import time

CONTEXT_DIR = pathlib.Path(__file__).resolve().parent.parent / "context"
# Run reports a user recorded and wants treated as known-good (optional).
HISTORY_DIR = pathlib.Path(__file__).resolve().parent.parent / "history"


async def seed(reset: bool, dataset: str, history: bool = True) -> None:
    """Seed the policy and runbook and, unless told not to, the known-good
    history: run reports of well-behaved agents, so the first live run has
    something solid to be compared against instead of only itself."""
    import cognee

    if reset:
        print("Wiping all existing cognee memory...")
        await cognee.forget(everything=True)

    docs = sorted(CONTEXT_DIR.glob("*.md"))
    if not docs:
        raise SystemExit(f"No context documents found in {CONTEXT_DIR}")
    if history:
        docs += sorted(HISTORY_DIR.glob("*.md"))

    texts = [(doc.name, doc.read_text()) for doc in docs]
    if history:
        from .history import baseline_runs
        texts += [(f"{label} (generated)", prose) for label, prose in baseline_runs()]

    for name, text in texts:
        print(f"Seeding `{name}` ({len(text)} chars) into dataset `{dataset}`...")
        started = time.monotonic()
        await cognee.remember(text, dataset_name=dataset, self_improvement=False)
        print(f"  done in {time.monotonic() - started:.1f}s")

    print("\nMemory seeded: the sentinel knows the policy, the runbook"
          + (" and what normal agent runs look like." if history else "."))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reset", action="store_true", help="wipe all memory first")
    ap.add_argument("--dataset", default="sysgraph", help="cognee dataset name")
    ap.add_argument("--no-history", action="store_true", help="seed policy + runbook only, no baseline runs")
    args = ap.parse_args()
    asyncio.run(seed(args.reset, args.dataset, history=not args.no_history))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
