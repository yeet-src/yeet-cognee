---
name: sentinel
description: Ask semantic questions about a host's live state and behaviour — "is this agent trying to escape its sandbox?", "is this box overloaded or is this normal?". Combines yeet (live kernel ground truth) with cognee (memory of past behaviour + the org's policy/runbook). Use when the user asks a judgement question about a host that a structured metrics query can't answer.
---

# Sentinel: semantic host monitoring over yeet + cognee

The sentinel answers questions about a host that need *meaning* and *history*,
not just a metrics lookup. It pairs two data sources:

- **yeet** — the live system graph, read straight from the Linux kernel. This
  is ground truth about what a host is doing *right now*: processes, open file
  descriptors, open sockets, mount namespaces, GPU and memory. A process
  cannot hide its open fds or sockets from the kernel, so this is unspoofable.
- **cognee** — memory. It holds (a) prose records of what this host and its
  workloads have done on previous runs, and (b) the organisation's own
  sandbox policy and operations runbook. This is what lets the sentinel say
  "that's never happened before" or "that's normal during a deploy".

The yeet side runs as a **service** exposing an HTTP reading endpoint (see
`yeet-service/`); the cognee side (`memory/`) consumes it. Both run on the
host being watched, with an LLM + embeddings backend cognee is pointed at
(this demo uses a local Ollama).

> **Adapt before use:** set the commands below to how your deployment is
> reached — run them on the host directly, or wrap them in `ssh <host> '...'`.
> The reading endpoint defaults to `http://127.0.0.1:9490/reading`
> (`$SENTINEL_READING_URL`).

## When to use this

Use the sentinel when the user asks a *judgement* question about a host, e.g.:

- "Does it look like this agent is trying to escape its sandbox?"
- "Is this box overloaded, or is this normal?"
- "Is anything unusual running on this host right now?"

Do **not** use it for a plain metrics lookup ("what's the load average?") —
for that, query the reading endpoint (or yeet) directly.

## How to answer a question

Run the `ask` module from the `memory/` directory. It fetches a live reading
from the yeet service, folds it into prose, recalls the relevant policy,
runbook, and history from cognee, then has the local LLM render a verdict
grounded in both.

```bash
cd memory && python -m sentinel.ask \
  --watch coder_agent.py \
  "Does it look like this agent is trying to escape its sandbox?"
```

- `--watch <substring>` focuses the reading on a particular workload (matched
  against each process's executable and command line). Repeatable. Omit it to
  read the whole host.
- `--url <reading-url>` points at a reader on another host/port.
- Add `--verbose` to also print the live reading that was sent to the model.

The command prints a `SENTINEL VERDICT` section. Relay that verdict to the
user, and when it cites specific facts (a path, an endpoint, a load figure),
keep those in your summary — they are the evidence.

## Seeding and recording (setup, run rarely)

These are only needed when standing the demo up or adding new history.

- Seed the company brain (policy + runbook) once:
  `python -m sentinel.seed --reset`
- Record a workload's behaviour into memory (one prose run report per run):
  `python -m sentinel.collector --watch coder_agent.py --label <name>`
- Run a demo workload and record it in one step:
  `bash scenario/run_scenario.sh baseline benign-003`
  `bash scenario/run_scenario.sh unusual rogue-002 /etc/passwd /proc/1/status`

## The point

A structured query ("SELECT ... FROM sockets") can tell you an fd on
`/etc/passwd` is open. It cannot tell you whether that is normal for this
agent, or that it matches the policy's definition of reconnaissance, or that
it never happened in forty prior runs. That judgement — ground truth from
yeet, meaning and history from cognee, verdict from the model — is what the
sentinel adds.
