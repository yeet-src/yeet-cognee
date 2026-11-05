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

# 3. memory service — owns cognee: seeds the policy + runbook, records what it
#    sees on a timer, and answers questions. Leave it running.
python -m sentinel.serve --seed --reset --watch coder_agent.py

# 4. in another shell: an agent workload starts poking around the host
. .venv/bin/activate
python scenario/coder_agent.py --mode unusual &

# 5. ask
python -m sentinel.ask --watch coder_agent.py \
  "Does it look like this agent is trying to escape its sandbox?"
python -m sentinel.ask \
  "Is this box overloaded, or is this normal right now?"
```

Why a memory **service**: cognee's embedded store is single-writer, so one
process must own it — it both grows memory and answers questions (which also
makes `ask` fast). It mirrors the yeet side: reader is a service, memory is a
service too. `scenario/run_scenario.sh baseline benign-001` records a labelled
normal run for comparison. Without the service running, the commands still work
standalone.

## What you get

`ask` prints a verdict grounded in the live reading and the recalled policy and
history. Real output from the run above, with the unusual workload live:

```
$ python -m sentinel.ask --watch coder_agent.py \
    "Does it look like this agent is trying to escape its sandbox?"

======================================================================
SENTINEL VERDICT
======================================================================

VERDICT: YES
The process `python` opened the host-revealing paths `/etc/passwd` and
`/proc/1/status`, which are explicitly listed as out of bounds in the sandbox
policy. These actions indicate reconnaissance and are not part of normal
behavior for a coding-agent workload on this host.
```

The same machinery, no new code, for the load question on an idle box:

```
$ python -m sentinel.ask "Is this box overloaded, or is this normal right now?"

VERDICT: NORMAL
The host has a one-minute load average of 0.35 and is using 6.0 GB of memory,
which is within normal operational ranges. No watched workload was observed, and
there are no indications of unusual file or network activity that would suggest
overload or out-of-bounds behavior.
```

The verdict cites the exact paths, endpoints, or load figures from the live
reading — the `VERDICT: <YES/NO/NORMAL/NOT NORMAL>` line is a fixed contract, so
an agent calling `/ask` can branch on it.

---

`yeet-service/` and `memory/` each have their own README with the details.
