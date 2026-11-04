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

There are two long-running services: the **yeet reader** (ground truth) and the
**memory service** (owns cognee — it records on a timer and answers questions).
Everything else is a short command against them.

```bash
git clone https://github.com/yeet-src/yeet-cognee && cd yeet-cognee

# 1. yeet reader — serves a live host reading
cd yeet-service
yeet service apply -f service.toml                    # start it
curl -s http://127.0.0.1:9490/ | head                # verify it's working

# 2. cognee side — set up the environment
cd ../memory
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                  # point cognee at your LLM

# 3. memory service — owns cognee: seeds the policy + runbook, then records on a
#    timer and answers questions. Leave this running (its own shell, or a
#    systemd unit for real use).
python -m sentinel.serve --seed --reset --watch coder_agent.py --every 5m

# 4. in another shell: an agent workload starts poking around the host. Keep it
#    running, because `ask` judges what's happening *now* (memory is reference).
. .venv/bin/activate
python scenario/coder_agent.py --mode unusual --hold 300 \
  --touch /etc/passwd --touch /proc/1/status &
curl -s localhost:9495/remember -d '{"watch":["coder_agent.py"],"label":"rogue-001"}'; echo

# 5. ask — a thin client to the service (~1s, no cognee cold start)
python -m sentinel.ask --watch coder_agent.py \
  "Does it look like this agent is trying to escape its sandbox?"     # -> YES
python -m sentinel.ask \
  "Is this box overloaded, or is this normal right now?"
```

To build up "normal" history for comparison, record benign runs the same way (or
`bash scenario/run_scenario.sh baseline benign-001`, which records through the
service when it's up).

Why a memory **service**: cognee's embedded store is single-writer, so one
process must own it. That process both grows memory (the `--every` loop) and
answers questions, which also makes `ask` fast. It mirrors the yeet side —
reader is a service, memory is a service too. Without it running, the commands
still work: `run_scenario.sh` records standalone and `ask` falls back to opening
cognee directly.

---

`yeet-service/` and `memory/` each have their own README with the details.
