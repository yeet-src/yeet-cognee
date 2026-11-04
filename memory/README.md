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

The **memory service owns cognee**: it seeds the brain, records on a timer, and
answers questions — all from one cognee instance. (cognee's embedded store is
single-writer, so a long-running recorder and a separate `ask` in two processes
would contend for one lock. One owner is the fix, and it mirrors the yeet side:
reader is a service, memory is a service too.)

```bash
# 1. start the memory service — seeds policy + runbook, then loops + serves.
#    Leave it running (its own shell; a systemd unit for real use).
python -m sentinel.serve --seed --reset --watch coder_agent.py --every 5m

# 2. in another shell (activate the venv again): a workload starts poking around.
#    Keep it running -- ask judges the LIVE state; memory is only reference.
. .venv/bin/activate
python scenario/coder_agent.py --mode unusual --hold 300 \
  --touch /etc/passwd --touch /proc/1/status &
curl -s localhost:9495/remember -d '{"watch":["coder_agent.py"],"label":"rogue-001"}'; echo

# 3. ask while it's live — a thin client to the service (~1s, no cognee cold start)
python -m sentinel.ask --watch coder_agent.py \
  "Does it look like this agent is trying to escape its sandbox?"     # -> YES
python -m sentinel.ask \
  "Is this box overloaded, or is this normal right now?"
```

`ask` judges the **live** reading (what the workload is doing *now*); the
recorded history and policy are reference for "is this normal / has this happened
before". So ask while the workload is running. To build normal history for
comparison, record benign runs the same way, or use
`bash scenario/run_scenario.sh baseline benign-001` (which records through the
service when it's up, then waits for its fixture to finish).

The service's routes:

- `GET /healthz` — liveness.
- `POST /ask {"question": "...", "watch": [...]}` → `{"verdict": "..."}`.
- `POST /remember {"watch": [...], "label": "..."}` → record one run now (what `run_scenario.sh` calls).
- `--host`/`--port` to bind (default `127.0.0.1:9495`); `--no-loop` serves without the timer; `--seed`/`--reset` seed on start.

`ask` talks to the service by default (`$SENTINEL_SERVE_URL` or
`http://127.0.0.1:9495`). **Without the service running**, everything still
works standalone: `run_scenario.sh` records with a one-shot collector and `ask`
opens cognee directly (or pass `--standalone`). Seed separately in that case
with `python -m sentinel.seed --reset`.

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
