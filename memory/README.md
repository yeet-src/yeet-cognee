# memory — the sentinel

The Python side: owns cognee, learns profiles, computes findings, asks the
LLM for verdicts, and takes reviews.

```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env                        # point cognee at your LLM and embeddings

python -m sentinel.serve --seed --reset --watch agent- --every 60
python -m sentinel learn --watch agent- --seconds 60
python -m sentinel ask --watch agent- "Which agents stand out?"
python -m sentinel review <id> --wrong --note "that endpoint is ours"
python -m sentinel console --watch agent-99        # live log plus bad/good/ask at a prompt
```

## The service

`sentinel.serve` is the one process that opens cognee (its embedded store is
single-writer). It seeds `context/*.md` with `--seed`, patrols every
`--every` seconds, and serves:

- `POST /ask {"question", "watch": [...], "window": 90}` → `{id, verdict, findings, ...}`
- `POST /learn {"watch": [...], "seconds": 60}` → the profiles updated
- `POST /review {"id", "correct": true|false, "note"}` → `202`; a rejection widens the profile, a confirmation compiles a rule
- `POST /observe {"watch": [...], "seconds": 5}` → what each workload did and the findings, no model
- `GET /rules` → the rules learned from confirmed reviews
- `GET /deny` → the compiled deny list and the patterns that can't be enforced

Records it keeps beside the package, all gitignored: `profiles/<role>.json`,
`rules.json`, `verdicts.jsonl`, `findings.jsonl`.

## Modules

- `reader.py` — client for the yeet reader's `/activity`
- `activity.py` — folds a window of events into one `Activity` per workload and renders it as prose
- `policy.py` — the code-level rules: sensitive paths, program directories, known hosts
- `binaries.py` — sha256 and owning package of a program
- `profile.py` — `Profile`: learn, findings, allow, describe
- `rules.py` — rules compiled from confirmed reviews: load, match, compile
- `deny.py` — rules + policy compiled into inode and basename deny sets for the LSM hook
- `ask.py` — the prompt and the recall query
- `memory.py` — the cognee owner
- `serve.py` — the service and the patrol
- `__main__.py` — the `ask`, `learn`, `review`, `tail`, `rules`, `console` CLI
