# yeet + cognee — semantic host monitoring

Answer judgement questions about a host that a metrics query can't — **"is this
agent trying to escape its sandbox?"**, **"is this box overloaded or is this
normal?"** — by combining:

- **yeet** — live ground truth, read straight from the kernel (processes, open
  fds, sockets, GPU, memory), served as an HTTP API.
- **cognee** — memory of what the host has done before, plus the org's sandbox
  policy and runbook.
- **an LLM** — the verdict, grounded in the reading and the retrieved memory.

![architecture](docs/architecture.svg)

*(open `docs/architecture.html` for the interactive version)*

## Run it

Both halves run on the host you want to watch. Needs yeet + `yeetd`, cognee,
and an LLM/embeddings backend (this demo uses local Ollama: `qwen3:8b` +
`nomic-embed-text`).

```bash
git clone https://github.com/yeet-src/yeet-cognee && cd yeet-cognee

# 1. yeet side — the reading service
cd yeet-service
yeet service apply -f service.toml                    # start it
curl -s http://127.0.0.1:9490/ | head                # verify it's working

# 2. cognee side
cd ../memory
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                  # point cognee at your LLM

python -m sentinel.seed --reset                       # load policy + runbook
bash scenario/run_scenario.sh baseline benign-001     # record normal behaviour
bash scenario/run_scenario.sh unusual  rogue-001 /etc/passwd /proc/1/status

# 3. ask
python -m sentinel.ask --watch coder_agent.py \
  "Does it look like this agent is trying to escape its sandbox?"
python -m sentinel.ask \
  "Is this box overloaded, or is this normal right now?"
```

## Keep memory growing — the memory service

The `run_scenario.sh` lines above each record **one** run (and `ask` above
opens cognee directly, which is fine when nothing else is). For memory that
builds on its own — and fast questions — run the **memory service**. One process
owns cognee: it polls the reader on a timer and remembers, *and* it answers
questions over HTTP. cognee's embedded store is single-writer, so one owner is
the right design — and it mirrors the yeet side, where the reader is a service
too.

```bash
# loop + serve (seed the brain on first start)
python -m sentinel.serve --seed --reset --watch coder_agent.py --every 5m
```

With it running, `python -m sentinel.ask …` becomes a thin client: it posts the
question to the service and gets a verdict in about a second, with no cognee
cold start. For a real deployment, run the service under systemd.

---

`yeet-service/` and `memory/` each have their own README with the details.
