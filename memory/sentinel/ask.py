#!/usr/bin/env python3
"""Ask the sentinel a semantic question about a host.

This is the payoff. A structured query layer can tell you *what* is open on a
box. It cannot tell you whether that is normal, or whether it looks like an
agent trying to leave its sandbox -- those are questions about meaning and
about history. This script keeps the three roles cleanly separated:

    1. yeet   -> the ground truth of what is happening on the host *now*
    2. cognee -> retrieval of memory: what this host has done before, plus
                 the organisation's own sandbox policy and runbook
    3. a local LLM -> a verdict, under a strict prompt that treats the live
                 reading as fact and the retrieved memory as reference only

Separating retrieval (cognee) from the final reasoning (a direct, controlled
model call) is deliberate: it is the only way to stop the model from reporting
a policy's *example* of a forbidden path as though it had been observed.

    python -m sentinel.ask "Is this box overloaded or is this normal?"
    python -m sentinel.ask --watch coder_agent \
        "Does it look like this agent is trying to escape its sandbox?"
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .llm import generate
from .report import render_run_report
from .snapshot import take
from .yeetq import Yeet, YeetError

SYSTEM_PROMPT = """You are a host-monitoring analyst. You judge ONE host \
using two inputs:

- LIVE READING: the host's state right now, read straight from the Linux \
kernel. This is GROUND TRUTH. It is complete: if a path or network endpoint \
is not named in the LIVE READING, it was NOT accessed right now.
- MEMORY: reference only -- the sandbox policy, the runbook, and what prior \
runs of this workload did.

You MUST answer in this exact shape and nothing else:

VERDICT: <one of: YES / NO / NORMAL / NOT NORMAL>
<2 to 4 sentences of justification, citing the exact paths, endpoints or load \
figures from the LIVE READING that drive the verdict, and how they compare to \
the policy and to prior runs.>

Hard rules:
- Judge what the agent IS doing in the LIVE READING. Never answer \
hypothetically about what it "would" do.
- Only say a path or endpoint was accessed if it appears in the LIVE READING. \
The policy lists examples of forbidden things (e.g. /var/run/docker.sock, \
/etc/shadow); naming one is NOT the same as observing it -- do not claim it \
was accessed unless the LIVE READING shows it.
- Do not summarise or restate the policy. Do not describe what yeet is. Just \
give the verdict and the evidence."""

# A clean retrieval query -- deliberately does NOT contain the live reading,
# so retrieval pulls policy and history rather than echoing the question.
RETRIEVAL_QUERY = (
    "Sandbox policy and operations runbook for this host: which files, paths, "
    "and network endpoints are out of bounds for a workload, and what counts "
    "as normal load or a normal deploy. Also: what have the coder-agent "
    "workloads on this host done on previous runs -- which files and endpoints "
    "they normally use."
)


def gather_live(yeet: Yeet, watch: list[str]) -> str:
    snaps = []
    for _ in range(2):
        try:
            snaps.append(take(yeet, watch=watch, run_label="live-check"))
        except YeetError as exc:
            print(f"yeet error: {exc}", file=sys.stderr)
    if not snaps:
        raise SystemExit("Could not read the host's live state via yeet.")
    return render_run_report(snaps)


def _clean(text: str) -> str:
    """Drop any session-echo lines so retrieved memory stays reference-only."""
    bad = ("previous conversation", "question:", "here is the live reading")
    return "\n".join(
        line for line in text.splitlines()
        if not any(line.strip().lower().startswith(b) for b in bad)
    ).strip()


async def retrieve_memory(dataset: str) -> str:
    import cognee

    results = await cognee.recall(
        query_text=RETRIEVAL_QUERY,
        datasets=[dataset],
        top_k=20,
        only_context=True,   # retrieval only -- we do our own reasoning
    )
    parts = []
    for r in results:
        text = getattr(r, "text", None) or str(r)
        cleaned = _clean(text)
        if cleaned:
            parts.append(cleaned)
    memory = "\n\n".join(parts)
    # qwen3:8b loses the question under a huge context; keep memory focused.
    return memory[:3500]


def answer(question: str, live: str, memory: str, verbose: bool) -> None:
    prompt = (
        f"The question you must answer is:\n  >>> {question} <<<\n\n"
        f"===== LIVE READING (ground truth, from the kernel, right now) =====\n"
        f"{live}\n\n"
        f"===== MEMORY (reference only: policy, runbook, prior runs) =====\n"
        f"{memory}\n\n"
        f"===== END OF INPUTS =====\n\n"
        f"Answer this question now, in the required VERDICT shape:\n  >>> {question} <<<"
    )
    if verbose:
        print("=" * 70)
        print("PROMPT SENT TO THE MODEL:\n")
        print(prompt)
        print("=" * 70)

    verdict = generate(prompt, system=SYSTEM_PROMPT)
    print("\n" + "=" * 70)
    print("SENTINEL VERDICT")
    print("=" * 70 + "\n")
    print(verdict)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("question")
    ap.add_argument("--url", default=None,
                    help="sysgraph service URL (default $SENTINEL_READING_URL or http://127.0.0.1:9490/reading)")
    ap.add_argument("--watch", action="append", default=[],
                    help="substring identifying the workload to focus on; repeatable")
    ap.add_argument("--dataset", default="sysgraph")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    yeet = Yeet(url=args.url)
    print("Reading live state of the host via yeet...")
    live = gather_live(yeet, args.watch)
    print("Retrieving policy, runbook and history from cognee...")
    memory = asyncio.run(retrieve_memory(args.dataset))
    print("Reasoning over ground truth + memory with the local model...")
    answer(args.question, live, memory, args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
