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

The **memory service owns cognee**: it seeds the brain, records runs, answers
questions and remembers reviews, all from one cognee instance. (cognee's
embedded store is single-writer, so a long-running recorder and a separate
`ask` in two processes would contend for one lock. One owner is the fix, and
it mirrors the yeet side: reader is a service, memory is a service too.)

```bash
# 1. start the memory service: seeds policy + runbook + generated known-good
#    history, then serves. Leave it running (its own shell; a systemd unit for real use).
python -m sentinel.serve --seed --reset --watch agent-        # add --every 5m to also record on a timer

# 2. in another shell (activate the venv again): a fleet of agents
python scenario/fleet.py                                      # agent-04 breaks policy, agent-09 is odd

# 3. remember their run: readings plus the activity log for the window
curl -s localhost:9495/remember -d '{"watch":["agent-"],"label":"fleet"}'

# 4. ask: a thin client to the service
python -m sentinel.ask --watch agent-04 "Is this agent breaking the sandbox policy?"
python -m sentinel.ask --watch agent-   "Which of these agents stand out, and why?"

# 5. review a verdict by its id; the review is remembered
python -m sentinel.ask --feedback <id> --correct --note "agent-04 is out of bounds"
```

`ask` judges two fresh readings plus the last 90 seconds of the activity log
(`--window`), with the recorded history, the policy and earlier reviews as
reference, so ask while the workload is running.

The service's routes:

- `GET /healthz` — liveness.
- `POST /ask {"question": "...", "watch": [...], "window": 90}` → `{"id", "verdict", "verdict_line", ...}`.
- `POST /remember {"watch": [...], "label": "...", "samples": 3, "interval": 8, "lead": 30}` → record one run now.
- `POST /feedback {"id": "...", "correct": true|false, "note": "..."}` → `202`, the review is remembered in the background.
- `--host`/`--port` to bind (default `127.0.0.1:9495`); `--every` records on a timer; `--seed`/`--reset`/`--no-history` seed on start.

## What goes into memory

Everything is prose with stable entity names (the host, `agent-04`, a path, an
endpoint), one document per run, because that is what cognee links across
time. A run report has, per watched workload:

- what it **held** across the readings: files, sockets, memory, fd count;
- what it **did** in the window, from the activity log: programs run, files
  opened inside and outside its workspace, host-revealing paths attempted
  (an attempt counts even if the kernel refused), paths written, endpoints
  dialled. Library loads and other noise every process makes are dropped.

Seeded alongside: `context/sandbox-policy.md`, `context/operations-log.md`,
the generated baseline runs from `sentinel/history.py`, and any run reports
you put in `history/`.

## Files

| File | What it does |
|---|---|
| `sentinel/serve.py` | the memory service: owns cognee; `/ask`, `/remember`, `/feedback` |
| `sentinel/ask.py` | the question: live reading + activity, recall, verdict; also the feedback client |
| `sentinel/collector.py` | record one run (readings + activity log) into cognee |
| `sentinel/snapshot.py` | a reading as prose |
| `sentinel/activity.py` | the activity log as prose: sorts actions into the sentences that matter |
| `sentinel/report.py` | folds a run's readings and activity into one document |
| `sentinel/history.py` | generates the known-good baseline runs for this host |
| `sentinel/seed.py` | seeds policy, runbook, history |
| `sentinel/llm.py` | the verdict model: whatever `LLM_*` cognee uses (Ollama native or OpenAI-compatible) |
| `sentinel/yeetq.py` | HTTP client for the reader's `/reading` and `/activity` |
| `scenario/coder_agent.py` | one agent fixture: `baseline`, `policy` or `suspicious` |
| `scenario/fleet.py` | a fleet of them |
| `scenario/run_scenario.sh` | one agent, recorded as a labelled run |
