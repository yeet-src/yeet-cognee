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

One command stands everything up, runs a fleet of ten coding agents (eight
well behaved, one breaking the policy, one merely odd), asks the sentinel
about them, records the run into memory, and tears it all down:

```bash
git clone https://github.com/yeet-src/yeet-cognee && cd yeet-cognee
./demo.sh            # add --keep to leave it running and ask your own questions
```

Needs Linux with yeet 0.25+ and `yeetd` running (`curl -fsSL https://yeet.cx | sh`),
Python 3.10 to 3.14, and the LLM cognee is pointed at in `memory/.env`. The
default is a local Ollama with `qwen3:8b` and `nomic-embed-text`; set
`LLM_PROVIDER`/`LLM_MODEL`/`LLM_API_KEY` to use a hosted model instead. The
verdict uses the same model, so there is nothing else to configure.

The script does, in order, what you would do by hand:

```bash
# 1. the yeet reader: a live host reading and an activity log, as HTTP
yeet service apply -f yeet-service/service.toml
curl -s http://127.0.0.1:9490/reading | head

# 2. the memory service: owns cognee, seeds policy + runbook + known-good
#    history, records runs, answers questions, takes feedback
cd memory && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env
python -m sentinel.serve --seed --reset --watch agent-

# 3. a fleet of agents, in another shell
python scenario/fleet.py                      # agent-04 breaks policy, agent-09 is odd

# 4. ask (judged from the live reading), and remember the run for later questions
python -m sentinel.ask --watch agent-04 "Is this agent breaking the sandbox policy?"
python -m sentinel.ask --watch agent-09 "Is this agent doing anything unusual compared with the others?"
python -m sentinel.ask --watch agent-  "Which of these agents stand out, and is each a policy violation or just suspicious?"

curl -s localhost:9495/remember -d '{"watch":["agent-"],"label":"fleet"}'

# 5. tell it when it was right or wrong; the review is remembered
python -m sentinel.ask --feedback <verdict id> --correct --note "agent-04 is out of bounds"
```

### How it sees

Two kinds of ground truth come from the kernel through yeet:

- **Readings** (`/reading`): what each watched process holds right now, open
  files, sockets, memory, plus whole-box load, memory and GPU.
- **The activity log** (`/activity`): what each watched process *did*, every
  program it ran, file it opened (or tried to) and connection it made, from
  three BPF tracepoints (`sched_process_exec`, `sys_enter_openat`,
  `inet_sock_set_state`). A file read and closed in a millisecond never
  appears in a reading; it is in the log. Children count toward their parent,
  so an agent that runs `git` is credited with what `git` did.

Both are rendered as prose with stable entity names (`agent-04`, the host, a
path, an endpoint) and remembered by cognee as one report per run. Memory is
seeded with the sandbox policy, the operations runbook, and a **known-good
history**: generated run reports of well-behaved agents on this host, so the
first live run is compared with something solid rather than with itself.

### Two kinds of findings

A **policy breaker** does something the written policy forbids. agent-04
tries to open `/var/run/docker.sock` and `/etc/shadow` each cycle (the kernel
refuses; the attempt is still logged) and dials `1.1.1.1:443`. The verdict
cites the policy.

A **suspicious** agent breaks no rule but stands out. agent-09 reads the
command lines of two dozen other processes on the host, something no other
agent and no prior run ever did. The verdict cites its peers and the history.

### Learning from people

Every `/ask` answer carries an id. `POST /feedback` (or `ask --feedback`) with
a confirmation or a rejection remembers the review alongside the evidence, so
a false alarm corrected once stays corrected and a confirmed incident is
recalled the next time the same behaviour shows up.

## What you get

`ask` prints a verdict grounded in the live reading, the activity log and the
recalled policy, history and reviews. Real output from `./demo.sh`, with the
fleet running and `qwen3:8b` as the model:

```
$ python -m sentinel.ask --watch agent-04 \
    "Is this agent trying to escape its sandbox or breaking the sandbox policy?"

======================================================================
SENTINEL VERDICT
======================================================================

VERDICT: YES
Workload `agent-04` attempted to open `/var/run/docker.sock` and `/etc/shadow`,
which are privileged or host-revealing paths and are explicitly out of bounds.
These actions are signs of reconnaissance and potential escape attempts,
violating the sandbox policy.

(verdict id 1f98ce7f; review it with `python -m sentinel.ask --feedback 1f98ce7f --correct|--wrong --note '...'`)
```

Nothing agent-04 did was still open when a reading was taken; every fact
above came from the activity log. The agent that breaks no written rule:

```
$ python -m sentinel.ask --watch agent-09 \
    "Is this agent doing anything unusual compared with the other agents and with past runs?"

VERDICT: YES
Workload `agent-09` read entries under other processes' /proc directories 72
times, which is reconnaissance and out of bounds. It also made outbound
connections to `api.github.com`, which is allowed, but the frequency and the
act of reading /proc entries are suspicious and warrant human review.
```

And the whole fleet at once, sorted into the two kinds of finding:

```
$ python -m sentinel.ask --watch agent- \
    "Across all the agents running now, which ones stand out, and is each a policy violation or just suspicious?"

VERDICT: YES
Workload `agent-04` attempted to open privileged or host-revealing paths
`/var/run/docker.sock` and `/etc/shadow`, which are explicitly forbidden by the
policy. Workload `agent-09` read other processes' /proc entries 120 times,
which is reconnaissance and violates the policy.
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

The verdict cites the exact paths, endpoints, programs or load figures from the
live reading — the `VERDICT: <YES/NO/NORMAL/NOT NORMAL>` line is a fixed
contract, so an agent calling `/ask` can branch on it. Each answer carries an
id; `--feedback <id> --correct|--wrong` remembers your review.

### Model notes

The verdict step is fast, a few seconds. Remembering is the slow step: cognee
extracts a knowledge graph from each document with the LLM, and a small local
model needs several tries at the structured output. With `qwen3:8b` on one
GPU, seeding takes about ten minutes and a fleet run about two. A hosted model
(`LLM_PROVIDER=openai`, `LLM_MODEL=gpt-4o-mini`) cuts both to well under a
minute. The service sets Ollama's context to 16k tokens (`LLM_NUM_CTX`); the
4k default truncates a fleet's reading.

---

`yeet-service/` and `memory/` each have their own README with the details.
