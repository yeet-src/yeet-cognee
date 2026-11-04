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

## The memory service (sentinel.serve) — the always-on path

The scenarios above are one-shot, and `ask` above opens cognee itself. That's
fine when nothing else is using the store — but cognee's embedded databases are
**single-writer**, so you can't run a long-running recorder in one process and
`ask` in another: they'd contend for one lock.

The fix is to let **one process own cognee**. `sentinel.serve` does that: it
runs the collector loop internally (polls the reader, remembers on a timer) and
answers questions over HTTP, all from a single cognee instance.

```bash
# seed the brain, then loop + serve (Ctrl-C to stop)
python -m sentinel.serve --seed --reset --watch coder_agent.py --every 5m

# background, surviving logout (use a systemd unit for real deployments):
nohup python -m sentinel.serve --watch coder_agent.py --every 5m \
  > serve.log 2>&1 &
```

- `GET /healthz` — liveness. `POST /ask {"question": "...", "watch": [...]}` → `{"verdict": "..."}`.
- Bind with `--host`/`--port` (default `127.0.0.1:9495`); `--no-loop` serves without recording.

With the service running, **`ask` is a thin client** — it posts the question and
gets a verdict in about a second, no cognee cold start:

```bash
python -m sentinel.ask --watch coder_agent.py "is this agent escaping its sandbox?"
```

`ask` talks to the service by default (`$SENTINEL_SERVE_URL` or
`http://127.0.0.1:9495`); if none is running it falls back to opening cognee
directly, or pass `--standalone` to force that.

## What each module does

| Module | Role |
|---|---|
| `sentinel/yeetq.py` | HTTP client for the reading service |
| `sentinel/snapshot.py` | one reading → **prose** (stable entity names cognee can link) |
| `sentinel/report.py` | a run's readings → one prose run report |
| `sentinel/collector.py` | record **one** run → `cognee.remember()` (scenarios, seeding history) |
| `sentinel/serve.py` | **the memory service**: owns cognee, loops + answers `/ask` |
| `sentinel/seed.py` | ingest `context/*.md` (the company brain) |
| `sentinel/ask.py` | thin client to the service (or standalone: reading + recall → verdict) |
| `sentinel/llm.py` | a direct, controlled call to the local LLM for the verdict |

The write path (remember) and the read path (retrieve, then reason) are
deliberately separate roles — but one process owns the store. See the three-role
table in the top-level README.

## Useful flags

```bash
# record more/faster readings, or just print the prose without ingesting
python -m sentinel.collector --watch coder_agent.py --label x --samples 6 --interval 5
python -m sentinel.collector --watch coder_agent.py --label x --dry-run

# ask a service on another host/port
python -m sentinel.ask --server http://other-host:9495 --watch coder_agent.py "..."

# standalone ask against a reader on another host/port, printing the full prompt
python -m sentinel.ask --standalone --url http://other-host:9490/reading \
  --watch coder_agent.py "..." --verbose
```

(`--url`, `--verbose` and `--dataset` apply to the standalone path; when asking
the service, it uses the reader and dataset it was started with.)

## The company brain

`context/sandbox-policy.md` and `context/operations-log.md` are plain Markdown
you edit to match your own host: what's out of bounds, what normal load looks
like, what a deploy does. `seed.py` ingests them; everything the sentinel
calls "normal" or "out of policy" traces back to these two files plus the
recorded history.
