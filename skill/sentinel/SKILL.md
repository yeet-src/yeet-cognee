---
name: sentinel
description: Ask judgement questions about the agents running on a host — "which of these agents is up to something?", "is this a policy violation or just odd?". Combines yeet (what each process does, from BPF tracepoints) with cognee (the sandbox policy, the learned profile of a well-behaved agent, and human reviews). Use when the user asks whether an agent or workload on a host is behaving, or asks to run the yeet + cognee demo.
---

# Sentinel: judging agent fleets with yeet + cognee

The sentinel answers questions about a host's workloads that need meaning
and history, not a metrics lookup. Three parts:

- **yeet** records every program a watched process runs, every file it opens
  or tries to, and every connection it makes, children included, from BPF
  tracepoints. An attempt is recorded whether or not the kernel allowed it.
- **cognee** remembers the sandbox policy, a **profile** per role of workload
  (programs by hash, directories, endpoints) learned from a period known to
  be well behaved, and human reviews of earlier verdicts.
- The **LLM cognee is configured with** writes the verdict from computed
  **findings**: `policy` (breaks a written rule or tampers with a program)
  and `novel` (outside the profile).

Everything runs on the host being watched. Layout: `yeet-service/` (the
reader), `memory/` (the sentinel, Python), `demo.sh`.

## Run the demo

```bash
./demo.sh --keep
```

It applies the reader, sets up the venv, starts the sentinel (seeding the
policy takes a minute or two on a local model), learns from a clean fleet,
starts a fleet where `agent-04` breaks policy and the last agent is odd,
prints one verdict and confirms it. `--keep` leaves it running. It checks for
yeet 0.25+ with `yeetd`, python3, and the LLM in `memory/.env`; if it reports
no Ollama, tell the user to start one or set `LLM_*` to another provider.

## Answer a question

With the sentinel running (the demo leaves it on `127.0.0.1:9495`):

```bash
cd memory && .venv/bin/python -m sentinel ask --watch agent-05 \
  "Is this agent doing anything the others are not?"
```

- `--watch <prefix>` picks workloads by a token of their exe, name or argv;
  `agent-` is the whole fleet. Repeatable.
- `--window <seconds>` is how much activity to judge (default 90).

It prints the verdict and an id. Relay the verdict and the facts it cites.
Say whether each finding is a **policy violation** or **suspicious**.

## Record a review

```bash
.venv/bin/python -m sentinel review <id> --correct --note "<why>"
.venv/bin/python -m sentinel review <id> --wrong   --note "<why>"
```

A rejection widens the role's profile. A confirmation is remembered as an
incident and compiled into file patterns (`python -m sentinel rules`) that
catch the same kind of thing next time. Policy only changes when a person
edits `memory/context/sandbox-policy.md`.

## Let the user try it by hand

`./play.sh` brings the stack up and opens tmux with the sentinel console on
the left and a bash treated as `agent-99` on the right. In the console the
user types `bad <why>` or `good <why>` to rule on the last verdict.

## Learn a profile

When the user says a period was clean:

```bash
.venv/bin/python -m sentinel learn --watch <prefix> --seconds 600
```

## Rarely

- Run only the fleet: `.venv/bin/python scenario/fleet.py --agents 5 --hold 300` (`--clean` for all well behaved).
- Rebuild the BPF object after editing `yeet-service/bpf/activity.bpf.c`: `make -C yeet-service`.

## The point

A metrics query can say an fd on `/etc/passwd` is open. It cannot see the
read that was over in a millisecond, say whether it is normal for this role
of agent, or match it to the policy. Ground truth from yeet, a learned
profile and the policy from cognee, the verdict from the model.
