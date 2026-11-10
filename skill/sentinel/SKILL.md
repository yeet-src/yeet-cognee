---
name: sentinel
description: Ask semantic questions about a host's live state and behaviour — "is this agent trying to escape its sandbox?", "which of these agents behaves differently?", "is this box overloaded or is this normal?". Combines yeet (live kernel ground truth: readings and a BPF activity log) with cognee (memory of past behaviour, the org's policy and runbook, and human reviews of earlier verdicts). Use when the user asks a judgement question about a host that a structured metrics query can't answer, or asks to run the yeet + cognee demo.
---

# Sentinel: semantic host monitoring over yeet + cognee

The sentinel answers questions about a host that need *meaning* and *history*,
not just a metrics lookup. It pairs two sources of truth with one memory:

- **yeet** reads the Linux kernel live. A **reading** is what each watched
  process holds right now (files, sockets, memory) plus whole-box state. The
  **activity log** is what it *did*: every program run, file opened or
  attempted, and connection made, from BPF tracepoints, children included.
- **cognee** remembers run reports of what workloads did before, the
  organisation's sandbox policy and runbook, a generated known-good history,
  and human reviews of earlier verdicts.
- The **LLM cognee is configured with** gives the verdict, under a strict
  prompt: the live reading is fact, memory is reference.

Everything runs on the host being watched. Repo layout: `yeet-service/` (the
reader, a yeet service), `memory/` (the cognee side, Python), `demo.sh`.

## Run the demo

From the repo root:

```bash
./demo.sh --keep
```

It applies the reader, sets up the venv, starts the memory service (seeding
takes a few minutes the first time), launches ten agents (`agent-04` breaks
policy, `agent-09` is merely odd), records the run and prints four verdicts.
`--keep` leaves it running for more questions. Prerequisites it checks: yeet
0.25+ with `yeetd`, python3, and the LLM in `memory/.env` reachable. If it
reports no Ollama, tell the user to start one or set `LLM_*` in `memory/.env`
to another provider.

## Answer a question

With the memory service running (the demo leaves it up on `127.0.0.1:9495`):

```bash
cd memory && .venv/bin/python -m sentinel.ask --watch agent-09 \
  "Is this agent doing anything unusual compared with the others?"
```

- `--watch <substring>` picks the workload(s) by command line; repeatable;
  `agent-` is the whole fleet. Omit it to judge the whole host.
- `--window <seconds>` is how much activity log to include (default 90).
- Without the service, add `--standalone` (opens cognee directly; do not do
  this while the service runs).

It prints a `SENTINEL VERDICT` block and a verdict id. Relay the verdict and
keep the facts it cites (paths, endpoints, programs, load figures): they are
the evidence. Say whether the finding is a **policy violation** (breaks a
written rule) or **suspicious** (differs from peers and history).

## Record feedback

When the user confirms or rejects a verdict, remember it:

```bash
.venv/bin/python -m sentinel.ask --feedback <id> --correct --note "<why>"
.venv/bin/python -m sentinel.ask --feedback <id> --wrong   --note "<why>"
```

## Setup and recording (rarely)

- Seed memory from scratch: `.venv/bin/python -m sentinel.seed --reset`
  (policy + runbook + generated history; `--no-history` for policy only).
- Record a run of some workload: `.venv/bin/python -m sentinel.collector --watch <x> --label <name>`
  or, with the service up, `curl -s localhost:9495/remember -d '{"watch":["<x>"],"label":"<name>"}'`.
- Run only the fleet: `.venv/bin/python scenario/fleet.py --agents 10 --hold 300`.
- Rebuild the BPF object after editing `yeet-service/bpf/activity.bpf.c`: `make -C yeet-service`.

## The point

A metrics query can say an fd on `/etc/passwd` is open. It cannot see the
read that was over in a millisecond, say whether it is normal for this agent,
match it to the policy's definition of reconnaissance, or notice it never
happened in forty prior runs. Ground truth from yeet, meaning and history
from cognee, verdict from the model: that is what the sentinel adds.
