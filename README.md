# yeet + cognee — a sentinel for agent fleets

Ten coding agents run on a box. Which one is up to something? A metrics query
can't tell you. This can: it watches each agent from the kernel, learns what a
well-behaved one looks like, and flags the ones that step out of line.

## Components

- **yeet** — what each agent *does*, from the kernel: every program it runs, file
  it touches, and connection it makes, child processes included.
- **cognee** — memory: the policy, each agent's learned normal, and your past
  rulings.
- **an LLM** — the verdict, from the activity and that memory.

![architecture](docs/architecture.svg)

## Quickstart — sample data, no kernel

Plays a recorded agent fleet through the real sentinel. No daemon, no root, no
fleet — just Python and a model.

```bash
git clone https://github.com/yeet-src/yeet-cognee && cd yeet-cognee
./replay.sh
```

At the console:

- You see what each agent did, in plain language.
- A finding pops: `⚠ DECISION NEEDED  agent-03 opened /var/run/docker.sock`.
- You rule on it: `bad <#> the docker socket is off limits`.
- `agent-06` tries the same thing → `✓ no action needed`, covered by your rule.

You need:

- Python 3.10–3.14.
- A model — default is a local Ollama (`qwen3:8b`); for a hosted one, set `LLM_*` in `memory/.env`.

The first run builds a baseline (a minute or two). Later runs start instantly.

## Run it on your own system

Watch the real processes on your host. Needs Linux and [`yeetd`][yeetd], the
kernel watcher.

```bash
./demo.sh --keep     # a built-in fleet, watched end to end
./play.sh            # the console plus your own shell, watched as agent-99
```

`--watch` takes a **program-name prefix** — it watches every process whose name
starts with it. The demo fleet is `agent-01…06`, watched with `agent-`. Point it
at whatever your agents are called — say `claude` for Claude Code sessions:

```bash
yeet service apply -f yeet-service/service.toml        # start the activity log
cd memory && python -m sentinel.serve --seed --watch claude --every 60 &
python -m sentinel learn  --watch claude --seconds 600   # learn normal from a window you trust
python -m sentinel ask    --watch claude "which agents stand out, and why?"
python -m sentinel review <id> --correct --note "out of bounds"
```

It picks up agents that are **already running** — no restart needed; the watcher
reads them from the kernel the moment it starts. It only sees activity from that
point on, though: history isn't retroactive.

`--enforce` blocks forbidden opens instead of just flagging them.

[yeetd]: https://yeet.cx

## How it works

- **Watch** — yeet reports every program, file, and connection, straight from
  the kernel. Even millisecond-long and child processes count.
- **Learn** — from a stretch you trust, it builds each agent's normal: the
  programs, directories, and endpoints it uses.
- **Find** — it compares live activity to the policy and that normal. A *policy*
  finding breaks a rule; a *novel* finding is something the agent has never done.
- **Judge** — the model reads the findings and the memory and answers one thing:
  does a human need to decide? It checks every interval, so nothing slips by.
- **Learn from you** — like case law. Call a finding bad and it becomes a rule
  that catches the same thing next time; call it fine and it joins the normal.
- **Enforce** — with `--enforce` it doesn't just flag, it blocks the open in the
  kernel, and symlinks or renamed paths can't sneak around it.

## Layout

- `yeet-service/` — the watcher (kernel + HTTP)
- `memory/sentinel/` — the sentinel: judging, learning, enforcing
- `memory/scenario/` — the demo fleet and the recorded `trace.json`
- `memory/context/` — the policy and runbook that seed memory
- `replay.sh` / `demo.sh` / `play.sh` — sample-data / live / by-hand
- `docs/cookbook.md` — the full walkthrough; `skill/sentinel/` — a Claude Code skill
