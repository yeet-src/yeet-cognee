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
    python -m sentinel.seed --reset     # wipe all memory first
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import time

CONTEXT_DIR = pathlib.Path(__file__).resolve().parent.parent / "context"


async def seed(reset: bool, dataset: str) -> None:
    import cognee

    if reset:
        print("Wiping all existing cognee memory...")
        await cognee.forget(everything=True)

    docs = sorted(CONTEXT_DIR.glob("*.md"))
    if not docs:
        raise SystemExit(f"No context documents found in {CONTEXT_DIR}")

    for doc in docs:
        text = doc.read_text()
        print(f"Seeding `{doc.name}` ({len(text)} chars) into dataset `{dataset}`...")
        started = time.monotonic()
        await cognee.remember(text, dataset_name=dataset)
        print(f"  done in {time.monotonic() - started:.1f}s")

    print("\nCompany brain seeded. The sentinel now knows the policy and runbook.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reset", action="store_true", help="wipe all memory first")
    ap.add_argument("--dataset", default="sysgraph", help="cognee dataset name")
    args = ap.parse_args()
    asyncio.run(seed(args.reset, args.dataset))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
