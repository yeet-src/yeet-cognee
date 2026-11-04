# memory — the cognee side

This is the **cognee half** of the sentinel. It consumes host readings from
the yeet service (`../yeet-service`), turns them into prose, remembers them,
and answers semantic questions by pairing a live reading with retrieved
memory and a local LLM.

## Setup

```bash
# a venv with cognee installed (python 3.12+ recommended)
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt   # just cognee; everything else is stdlib

cp .env.example .env              # point cognee at your LLM + embeddings
```

Make sure the yeet reader is running (see `../yeet-service/README.md`). On the
same machine the default (`http://127.0.0.1:9490/reading`) just works; point
`SENTINEL_READING_URL` elsewhere only if the reader is on another host or port.

## The loop

```bash
# 1. teach it the company brain — the sandbox policy + ops runbook
python -m sentinel.seed --reset

# 2. grow memory: record how the workload normally behaves
#    (the collector polls the reader and remembers one prose run report)
bash scenario/run_scenario.sh baseline benign-001
bash scenario/run_scenario.sh baseline benign-002

# 3. record a run that pokes around the host
bash scenario/run_scenario.sh unusual rogue-001 /etc/passwd /proc/1/status

# 4. ask
python -m sentinel.ask --watch coder_agent.py \
  "Does it look like this agent is trying to escape its sandbox?"
python -m sentinel.ask \
  "Is this box overloaded, or is this normal right now?"
```

Each `run_scenario.sh` records **one** short run. `--reset` on seed wipes
memory, so run it once, not before every scenario.

## Keeping memory growing (the long-running collector)

The scenarios above are one-shot. To make cognee's picture of the host build on
its own, run the collector in **loop mode** as a background process — this is
the only long-running piece on the cognee side:

```bash
# remember a fresh run report every 5 minutes, forever (Ctrl-C to stop)
python -m sentinel.collector --watch coder_agent.py --loop --every 5m

# in the background, surviving logout:
nohup python -m sentinel.collector --watch coder_agent.py --loop --every 5m \
  > collector.log 2>&1 &
```

Each cycle takes `--samples` readings, folds them into one prose run report
labelled with a timestamp, and hands it to `cognee.remember()`. A cycle that
can't reach the reader logs and retries rather than exiting. For a real
deployment, wrap this in a systemd unit instead of `nohup`.

## What each module does

| Module | Role |
|---|---|
| `sentinel/yeetq.py` | HTTP client for the reading service |
| `sentinel/snapshot.py` | one reading → **prose** (stable entity names cognee can link) |
| `sentinel/report.py` | a run's readings → one prose run report |
| `sentinel/collector.py` | poll the reader on a timer → `cognee.remember()` |
| `sentinel/seed.py` | ingest `context/*.md` (the company brain) |
| `sentinel/ask.py` | live reading + `cognee.recall()` → LLM verdict |
| `sentinel/llm.py` | a direct, controlled call to the local LLM for the verdict |

`collector.py` is the write path — "event happened, remember it." `ask.py` is
the read path — retrieve, then reason. They are deliberately separate: see the
three-role table in the top-level README.

## Useful flags

```bash
# record more/faster readings, or just print the prose without ingesting
python -m sentinel.collector --watch coder_agent.py --label x --samples 6 --interval 5
python -m sentinel.collector --watch coder_agent.py --label x --dry-run

# point at a reader on another host/port
python -m sentinel.ask --url http://other-host:9490/reading --watch coder_agent.py "..."

# see exactly what the model was given
python -m sentinel.ask --watch coder_agent.py "..." --verbose
```

## The company brain

`context/sandbox-policy.md` and `context/operations-log.md` are plain Markdown
you edit to match your own host: what's out of bounds, what normal load looks
like, what a deploy does. `seed.py` ingests them; everything the sentinel
calls "normal" or "out of policy" traces back to these two files plus the
recorded history.
