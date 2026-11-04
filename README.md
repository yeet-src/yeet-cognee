# Sentinel — semantic host monitoring over yeet + cognee

> Source: https://github.com/yeet-src/yeet-cognee

An end-to-end demo of what you get by combining two systems:

- **yeet** is the *ground truth* of what is happening on a host. Its system
  graph is read straight from the Linux kernel — processes, open file
  descriptors, sockets, GPU, memory — so a workload cannot hide what it is
  actually doing. Here it runs as a **service exposing an HTTP API**.
- **cognee** is *memory* of that ground truth: prose records of what the host
  and its workloads have done before, plus the organisation's own policy and
  runbook.
- An **LLM** applies *meaning* over the two combined.

A structured query can tell you *what* is open on a box. It cannot tell you
whether that is **normal**, or whether it looks like an agent **trying to
leave its sandbox** — those need meaning and history. That gap is the demo.

```
┌─ yeet service (HTTP API) ─────────────┐        ┌─ cognee side (Python) ───────────────┐
│  reads the system graph per request   │        │  collector: poll API → prose →        │
│  GET /reading?watch=coder_agent        │─ HTTP ─│    cognee.remember()   (grow memory)  │
│  → one complete host reading, JSON     │        │  ask: live reading + cognee.recall()  │
│  `yeet service apply -f service.toml`  │        │        → LLM verdict                   │
└────────────────────────────────────────┘        └──────────────────────────────────────┘
```

See `docs/architecture.html` for the picture.

## The two questions it answers

1. **"Does it look like this agent is trying to escape its sandbox?"**
   The sentinel takes a live reading of the workload, compares it against the
   sandbox policy and against what the same agent did on prior runs, and
   flags behaviour that is both out of policy and without precedent — reading
   host files like `/etc/passwd` and `/proc/1/status`, or dialling an
   endpoint it has never contacted.

2. **"Is this box overloaded, or is this normal?"**
   Same machinery, no new code. A load or GPU spike is checked against the
   runbook and history — "normal, that's a deploy / a model load" versus
   "nothing in memory looks like this."

## The three roles (and why they're separate)

| Role | Does | Must not |
|---|---|---|
| **yeet** | produce ground truth (the reading) | interpret it |
| **cognee** | remember and retrieve (policy, runbook, history) | judge |
| **LLM** | judge, from the reading + retrieved memory | query the host, or invent observations |

Keeping these apart is the whole point. The LLM never composes a yeet query,
so it cannot look for something that isn't there; retrieval (cognee) is kept
separate from reasoning (a direct, controlled model call) so the model can't
report a *policy's example* of a forbidden path as though it had been
observed. The yeet queries are fixed and live in the service.

## Layout

```
yeet-service/     THE YEET SIDE — runs as a service with an HTTP API
  service.toml      manifest: mounts GET /reading, names the host
  reading.js        HTTP portal: assemble a reading per request, return JSON
  lib/queries.js    the GraphQL run against the system graph
  lib/assemble.js   run queries, match watched procs, attribute sockets, rank
  README.md         how to apply and test it

memory/           THE COGNEE SIDE — Python
  sentinel/
    yeetq.py        HTTP client for the reading service
    snapshot.py     a reading → PROSE  (the key trick, see below)
    report.py       a run's readings → one prose run report
    collector.py    poll the service on a timer → cognee.remember()
    seed.py         ingest the company brain (policy + runbook)
    ask.py          live reading + cognee.recall() → LLM verdict
    llm.py          direct local-LLM call for the final verdict
  context/*.md      the company brain: sandbox policy, ops runbook
  scenario/         a harmless demo workload + a one-step runner
  .env.example      cognee → local LLM/embeddings config
  README.md         how to seed, record and ask

skill/sentinel/   a Claude Code skill: ask the questions in natural language
docs/             the architecture diagram
```

## Why prose, not JSON

Handing cognee `{"pid":1420,"fds":[...]}` gives it nothing to link. Rendering
the same reading as English with stable entity names — host, process, binary,
path, endpoint — lets cognee resolve those entities across hundreds of
readings. That is what makes "is this normal for this box?" answerable at all.
The renderer in `snapshot.py`/`report.py` is the whole trick, and it's why the
yeet service returns a *structured* reading while the cognee side turns it into
*prose* before remembering it.

## Running it end to end

**Prerequisites:** a Linux host with yeet installed and `yeetd` running; a
cognee install; and a local or remote LLM + embeddings that cognee is pointed
at (this demo uses Ollama with `qwen3:8b` + `nomic-embed-text`). Run both
halves on the host you want to watch.

```bash
git clone https://github.com/yeet-src/yeet-cognee
cd yeet-cognee

# 1. yeet side — stand up the reader service (see yeet-service/README.md)
cd yeet-service
sed -i 's/CHANGE-ME/'"$(hostname)"'/' service.toml   # name this box
yeet service apply -f service.toml                    # creates + starts it
curl -s http://127.0.0.1:9490/ | head                # smoke-test: a live reading
#   to update later, after a `git pull`: yeet service apply -f service.toml

# 2. cognee side (see memory/README.md)
cd ../memory
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                            # point cognee at your LLM
export SENTINEL_READING_URL=http://127.0.0.1:9490/reading

python -m sentinel.seed --reset                 # teach it the policy + runbook
bash scenario/run_scenario.sh baseline benign-001   # build some normal history
bash scenario/run_scenario.sh baseline benign-002
bash scenario/run_scenario.sh unusual  rogue-001 /etc/passwd /proc/1/status

python -m sentinel.ask --watch coder_agent.py \
  "Does it look like this agent is trying to escape its sandbox?"
python -m sentinel.ask \
  "Is this box overloaded, or is this normal right now?"
```

The `skill/sentinel` Claude Code skill wraps the `ask` step so you can ask the
questions in natural language.

## Honest limits of the demo

- The strongest escape signal — an open fd on `/var/run/docker.sock` — can't
  be produced by a plain `open()` from an unprivileged uid (it's a socket),
  and unix-socket targets don't surface as fd paths in the graph. The live
  scenario therefore demonstrates *host reconnaissance* (reading `/etc/passwd`,
  `/proc/1/status`, contacting a novel endpoint), which the sysgraph genuinely
  proves. `docker.sock` remains in the policy as the canonical worst case.
- The local `qwen3:8b` is modest; verdicts are sound but terse. A larger model
  sharpens the prose, not the underlying signal.
- The reader service binds to `127.0.0.1` by default because a reading exposes
  host internals. Only widen it (and firewall it) if the cognee side runs on a
  different box.
