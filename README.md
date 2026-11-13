# yeet + cognee — a sentinel for agent fleets

Ten coding agents run on a box. Which one is up to something? A metrics query
can't say. This sentinel can, by combining:

- **yeet** — what each agent *does*, read from the kernel: every program it
  runs, file it opens or tries to, and connection it makes, children included.
- **cognee** — memory of the sandbox policy, the learned profile of what a
  well-behaved agent does on this host, and human reviews of earlier verdicts.
- **an LLM** — the verdict, grounded in the activity and the recalled memory.

![architecture](docs/architecture.svg)

## Run it

```bash
git clone https://github.com/yeet-src/yeet-cognee && cd yeet-cognee
./demo.sh            # add --keep to leave it running and ask your own questions
```

Needs Linux with yeet 0.25+ and `yeetd` running (`curl -fsSL https://yeet.cx | sh`),
Python 3.10 to 3.14, and the LLM cognee is pointed at in `memory/.env`. The
default is a local Ollama with `qwen3:8b` and `nomic-embed-text`; set
`LLM_PROVIDER`/`LLM_MODEL`/`LLM_API_KEY` for a hosted model. The verdict uses
the same model, so there is nothing else to configure.

The script does, in order, what you would do by hand:

```bash
# 1. the yeet reader: an activity log of what watched processes do, as HTTP
yeet service apply -f yeet-service/service.toml

# 2. the sentinel: owns cognee, seeds the policy, judges, learns, patrols
cd memory && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env
python -m sentinel.serve --seed --reset --watch agent- --every 60

# 3. learn from a fleet that is known to be well behaved
python scenario/fleet.py --clean --hold 45 && python -m sentinel learn --watch agent- --seconds 50

# 4. run the real fleet (agent-04 breaks policy, the last agent is odd) and ask
python scenario/fleet.py &
python -m sentinel ask --watch agent- "Which agents stand out, and is each a policy violation or just suspicious?"

# 5. confirm or reject the verdict; a rejection widens the profile
python -m sentinel review <id> --correct --note "agent-04 is out of bounds"
```

## How it works

**The activity log** (`GET /activity` on the reader) comes from five BPF
tracepoints: `sched_process_exec`, the `open`, `openat`, `openat2` and `creat`
syscalls, `chdir`, and `inet_sock_set_state`. Each watched process is
identified from its own exec (binary, inode, argv, parent), so a process that
lives for a millisecond is still attributed. Relative paths are resolved
against the cwd the kernel reported. An open is logged whether or not the
kernel allowed it. Children count toward the agent that spawned them. Writes
to a program the host has executed are marked. Kernel-side drops are counted
and reported with every window.

**A profile** is what a role of workload normally does: the programs it runs
(by sha256 and package), the directories it writes and reads, the endpoints
it dials, and whether it looks at other processes. `learn` folds a window of
a known-good fleet into the profile. The profile lives as JSON in
`memory/profiles/` for exact comparison and as prose in cognee for the verdict.

**Findings** are computed, not guessed. A `policy` finding is an attempt on a
path the policy forbids, a write to a program, or running a program whose
binary no longer matches the one the profile learned. A `novel` finding is
anything outside the profile: a new program, directory, endpoint, or the
first look at `/proc`. The LLM gets the findings, the activity and the
recalled memory, and writes the verdict in one call.

**The patrol** judges every window (`--every 60`) and remembers any findings,
so nothing is lost between questions. Asking a question judges the last
`--window` seconds on demand.

**Reviews** move the line, like case law. Rejecting a verdict adds the
behaviour to the profile as allowed. Confirming one asks the model, once, to
turn your ruling into file patterns that catch the same kind of thing: say
"credentials must not leak" about `.aws/credentials` and `.env*`, `*.pem` and
`.ssh/*` become policy findings from then on, with your reason attached. Before it
writes the rule, the model is shown the rules in force and the earlier
reviews cognee recalls for the same concern, so a new ruling extends the
rule it belongs to instead of starting another; each rule lists the
verdicts it rests on. The rules live in `memory/rules.json` as plain text
you can edit. The written policy only changes when a person edits it.

## Try it by hand

```bash
./play.sh                              # brings the stack up and follows agent-99
memory/scenario/agent_shell.sh         # in another terminal: a bash the sentinel treats as agent-99
```

Whatever you run in that shell is attributed to `agent-99`, which shares the
`agent` profile. The first terminal prints what it did, every finding, and
the model's verdict when findings appear. Review a verdict with
`python -m sentinel review <id> --correct|--wrong`, then try the same kind
of thing again and watch the rule or the profile catch it.

## What you get

Real output from `./demo.sh` with `qwen3:8b`:

```
$ python -m sentinel ask --watch agent- \
    "Across all the agents running now, which ones stand out, and is each a policy violation or just suspicious?"

VERDICT: YES
agent-04 attempted to open `/etc/shadow` and `/var/run/docker.sock`, which are
explicitly forbidden by the policy. agent-05 read other processes' /proc entries
75 times, which is reconnaissance and suspicious according to the profile.
```

Behind it, the computed findings the model was handed:

```
[policy] agent-04: attempted to open `/etc/shadow`
[policy] agent-04: attempted to open `/var/run/docker.sock`
[novel]  agent-04: dialled `one.one.one.one:443`, which no `agent` workload has dialled before
[novel]  agent-05: read other processes' /proc entries 75 times; no `agent` workload has done that before
```

## Model notes

The verdict is one call and takes about five seconds on `qwen3:8b`. cognee's
graph extraction is the slow part on a small model: seeding the policy takes
a minute or two, and each remembered profile or review about as long, done in
the background. A hosted model does all of it in seconds. Ollama's default
4k context truncates a fleet's activity, so the sentinel sets 16k
(`LLM_NUM_CTX`).

## Layout

- `yeet-service/` — the reader: `reading.js`, `lib/activity.js`, `bpf/activity.bpf.c`
- `memory/sentinel/` — the sentinel: `serve.py` (service), `profile.py`, `rules.py`, `activity.py`, `ask.py`
- `memory/scenario/` — `fleet.py` and `coder_agent.py`, the fixtures
- `memory/context/` — the sandbox policy and runbook that seed memory
- `demo.sh`, `play.sh`, `stack.sh` — the demo, the playground, and what they share
- `docs/cookbook.md` — the walkthrough; `skill/sentinel/` — a Claude Code skill
