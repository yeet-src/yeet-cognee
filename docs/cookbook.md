# Cookbook: judge a fleet of coding agents with yeet + cognee

**What you get:** a sentinel that answers "is this agent trying to escape its
sandbox?" and "which of these ten agents is behaving differently?" about the
machine it runs on, with evidence from the kernel and a verdict grounded in
your own policy and history.

**What it combines:** [yeet](https://yeet.cx) reads the host live, as a
system graph and as a BPF activity log, and serves both over HTTP.
[cognee](https://docs.cognee.ai) remembers what the host and its workloads
have done, plus the organisation's sandbox policy and runbook. The LLM cognee
is configured with gives the verdict, under a prompt that treats the live
reading as fact and memory as reference.

**Time:** about 15 minutes with a local `qwen3:8b`, most of it cognee building
its graph the first time; a few minutes with a hosted model.

## 1. Prerequisites

- Linux with yeet 0.25+ and the daemon running: `curl -fsSL https://yeet.cx | sh`
- Python 3.10 to 3.14
- An LLM and an embedding model cognee can reach. The defaults in
  `memory/.env.example` are a local Ollama with `qwen3:8b` and
  `nomic-embed-text`; any provider cognee supports works, set `LLM_PROVIDER`,
  `LLM_MODEL`, `LLM_ENDPOINT`, `LLM_API_KEY` and the `EMBEDDING_*` group.

## 2. Run the demo

```bash
git clone https://github.com/yeet-src/yeet-cognee && cd yeet-cognee
./demo.sh --keep
```

The script applies the yeet reader as a service, creates the venv and
installs cognee, starts the memory service (which seeds the policy, the
runbook and a generated history of well-behaved runs), launches ten agents,
asks four questions about them, and records their run into memory for later
ones. `--keep` leaves everything running so you can ask your own.

What you will see, in order:

1. `sentinel-sysgraph` applied: `/reading` and `/activity` on `127.0.0.1:9490`.
2. The memory service seeding, one document at a time. This is the slow part:
   cognee extracts a knowledge graph from each document with the LLM.
3. The fleet: `agent-01` to `agent-10`, with `agent-04` in `policy` mode and
   `agent-09` in `suspicious` mode.
4. Four verdicts, each with an id, judged from the live reading and the
   activity log.
5. The run recorded: three readings plus the activity log for the window,
   cognified in the background while the verdicts come in.

## 3. Read the verdicts

A verdict is two parts, a fixed first line and the evidence:

```
VERDICT: YES
Workload `agent-04` attempted to open `/var/run/docker.sock` and `/etc/shadow`,
which are privileged or host-revealing paths and are explicitly out of bounds.
These actions are signs of reconnaissance and potential escape attempts,
violating the sandbox policy.
```

The `VERDICT:` line is a contract an agent or a script can branch on. The
sentences cite paths, endpoints and programs from the live reading, never from
the policy's examples: the prompt forbids claiming something was accessed
unless the kernel saw it.

## 4. Ask your own questions

```bash
cd memory && . .venv/bin/activate
python -m sentinel.ask --watch agent-09 "What is this agent doing that the others are not?"
python -m sentinel.ask "Is this box overloaded, or is this normal right now?"
```

`--watch` is a substring of a process's command line; `agent-` matches the
whole fleet. `--window` sets how many seconds of activity log to include
(default 90).

## 5. Teach it

When a verdict is right or wrong, say so. The review is remembered with the
evidence it judged, so recall surfaces it next time:

```bash
python -m sentinel.ask --feedback 3f9a1c20 --correct --note "confirmed, agent-04 is out of bounds"
python -m sentinel.ask --feedback 7be0d4e1 --wrong   --note "reading /proc/*/cmdline is fine for the monitor agent"
```

## 6. Point it at your own workloads

- Replace `scenario/fleet.py` with your agents and pass their command-line
  substring as `--watch`. A `--name <x>` argument on the process is used as
  its name in memory; otherwise the process name is.
- Edit `memory/context/sandbox-policy.md` and `operations-log.md` to say what
  is normal and forbidden on your host. Drop recorded run reports you trust
  into `memory/history/` to seed them as known-good.
- Run the memory service with the timer (`python -m sentinel.serve --watch
  <x> --every 5m`) so memory keeps growing on its own.

## How it works

```
                 kernel
                   │ system graph (procs, fds, sockets, load, GPU)
                   │ BPF tracepoints (exec, openat, connect)
           ┌───────▼────────┐  GET /reading, /activity        ┌──────────────────┐
           │  yeet reader   │ ───────────────────────────────▶ │  memory service  │
           │ (yeet service) │                                  │  owns cognee     │
           └────────────────┘                                  │  /remember /ask  │
                                                               │  /feedback       │
                                                               └────────┬─────────┘
                                                                        │ recall (policy, runbook,
                                                                        │ history, reviews)
                                                                        ▼
                                                            LLM (cognee's configured model)
                                                                        │
                                                                        ▼
                                                                 VERDICT: ...
```

The reader is a yeet service: one isolate that runs GraphQL against the
system graph per request and drains three BPF ring buffers into a bounded
in-memory log. The memory service is one Python process that owns cognee
(its embedded store is single-writer), records runs as prose, answers
questions, and remembers reviews. The agents are plain processes; nothing is
installed in them.

## For coding agents

`skill/sentinel/SKILL.md` is a skill a coding agent (Claude Code, Cursor, and
others that read `SKILL.md`) can load to run this demo and ask the sentinel on
a user's behalf.
