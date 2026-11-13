# Cookbook: watch a fleet of coding agents with yeet + cognee

**What you get:** a sentinel that answers "which of these agents is up to
something, and is it breaking policy or just acting oddly?" about the machine
it runs on, with evidence from the kernel and a verdict grounded in your own
policy and in what your agents normally do.

**What it combines:** [yeet](https://yeet.cx) records what every watched
process does, from BPF tracepoints, and serves it over HTTP.
[cognee](https://docs.cognee.ai) remembers the sandbox policy, the learned
profile of a well-behaved agent on this host, and human reviews. The LLM
cognee is configured with writes the verdict.

**Time:** about five minutes with a local `qwen3:8b`, most of it cognee
extracting the policy the first time; under two with a hosted model.

## Fastest path: replay on sample data (no kernel, no fleet)

To see it work before wiring up the kernel reader, replay a recorded trace of
an agent fleet through the real sentinel — the findings, the verdict and the
case-law loop — with no yeetd, no root and no fleet. You only need Python and
the model cognee is configured with.

```bash
git clone https://github.com/yeet-src/yeet-cognee && cd yeet-cognee
./replay.sh
```

The first run seeds the policy and learns a baseline from the trace, then saves
it; every later run restores it in a second. The console then plays the trace:
`agent-03` breaks policy, `agent-05` looks suspicious, and `agent-06` repeats
`agent-03` so a rule you make covers it. Everything below is the same thing
against a live kernel.

## 1. Prerequisites

- Linux with yeet 0.25+ and the daemon running: `curl -fsSL https://yeet.cx | sh`
- Python 3.10 to 3.14
- An LLM and an embedding model cognee can reach. The defaults in
  `memory/.env.example` are a local Ollama with `qwen3:8b` and
  `nomic-embed-text`; any provider cognee supports works: set `LLM_PROVIDER`,
  `LLM_MODEL`, `LLM_ENDPOINT`, `LLM_API_KEY` and the `EMBEDDING_*` group.

## 2. Run the demo

```bash
git clone https://github.com/yeet-src/yeet-cognee && cd yeet-cognee
./demo.sh --keep
```

What you will see, in order:

1. `sentinel-sysgraph` applied: `/activity` and `/reading` on `127.0.0.1:9490`.
2. The sentinel building its baseline: seeding the policy into cognee and
   learning a clean fleet's profile. This is the slow part on a local model —
   and it only happens once, because the result is saved and later runs restore
   it in a second (`--rebaseline` forces a fresh build).
3. A clean fleet of five agents working for 45 seconds, then `learn`: the
   `agent` profile is written from what they did (skipped when a baseline was
   restored).
4. The real fleet: `agent-04` in `policy` mode, `agent-05` in `suspicious`
   mode, the rest well behaved.
5. One verdict, with the findings it was computed from, and one review.

## 3. Read the verdict

The sentinel answers one question — does a human need to decide? — and leads
with the answer:

```
⚠ DECISION NEEDED  agent-04 attempted to open `/etc/shadow` and
`/var/run/docker.sock`, which the policy forbids, and agent-05 read other
processes' /proc entries, which no agent has done before. Both need a call.
```

When nothing is wrong it says so and asks nothing of you:

```
✓ no action needed  every agent stayed within its learned profile and the policy.
```

The findings behind a decision are listed beneath it, numbered so you can rule
on them:

```
 1. attempted to open `/etc/shadow`              (broke a rule — agent-04)
 2. dialled `one.one.one.one:443`                (unusual — agent-04)
 3. read other processes' /proc entries          (unusual — agent-05)
```

A "broke a rule" finding breaks the written policy or tampers with a program.
An "unusual" finding is outside the learned profile and stands out until you
rule on it. Under the banner, the `/ask` response still carries a machine-
readable `VERDICT: YES/NO` line for scripts to branch on, and every verdict is
appended to `memory/verdicts.jsonl`.

## 4. Ask your own questions

```bash
cd memory && . .venv/bin/activate
python -m sentinel ask --watch agent-05 "What is this agent doing that the others are not?"
python -m sentinel ask --watch agent-   "Has anything changed in the last ten minutes?" --window 600
```

`--watch` matches a token of the process's exe, name or argv by prefix;
`agent-` matches the whole fleet. `--window` is how many seconds of activity
to judge (default 90).

## 5. Teach it

```bash
python -m sentinel review 3f9a1c20 --correct --note "confirmed, agent-04 is out of bounds"
python -m sentinel review 7be0d4e1 --wrong   --note "the monitor agent is allowed to read /proc"
```

A rejection folds the workload's behaviour into its role's profile, so the
same thing is not a finding next time. A confirmation is remembered as an
incident and, once, the model turns your note into file patterns that catch
the same kind of thing:

```
$ python -m sentinel rules
*/.aws/credentials, */.env*, *.pem, */.ssh/*, */.config/*  (credential files must never be read; from verdict a790209c)
```

From then on reading `.env.production` is a policy finding with your reason
attached. Confirm a later verdict about a deploy key and the model, shown
this rule and the earlier review, extends it rather than writing a second:
the rule then cites both verdicts as its precedents. Rules are plain JSON in `memory/rules.json`; edit or delete them
freely. Policy is never changed by a review: edit
`memory/context/sandbox-policy.md` for that.

## 5b. Play with it

```bash
./play.sh      # two tmux panes: the sentinel console, and a bash it treats as agent-99
```

Try reading a secret file, dialling an address, copying a binary in the
right pane. The left pane shows what the shell did, each finding in red
(policy) or yellow (novel), and the verdict. Type `bad <why>` or
`good <why>` there to rule on it, `ask <question>` to ask, `rules` to list
the rules. Then do the same kind of thing again.

Run `./play.sh --enforce` instead and the denied opens actually fail: a BPF
LSM `file_open` hook denies them with `EPERM`. It matches the resolved file,
by inode for concrete files and by basename for patterns, so a symlink, a
relative path, a hard link or a forked child can't get around it. Only
watched processes are blocked. `python -m sentinel rules` and `GET /deny`
show what is enforced.

## 6. Point it at your own workloads

- Pass your agents' process name or a `--name` prefix as `--watch`. A
  workload's role is its name with any trailing number stripped, so
  `builder-01` and `builder-02` share the `builder` profile.
- Agents already running are picked up with no restart — the watcher reads
  them from the kernel as soon as it starts. It only sees activity from that
  point forward, though; history isn't retroactive.
- Learn from a period you trust: `python -m sentinel learn --watch builder --seconds 600`.
- Edit `memory/context/sandbox-policy.md` to say what is forbidden on your
  host. Add to `KNOWN_HOSTS` in `memory/sentinel/policy.py` so approved
  endpoints are named rather than numbered.
- Leave the service patrolling (`--every 60`): every window is judged and any
  finding is remembered, whether or not anyone asks.

## How it works

```
                 kernel
                   │ BPF tracepoints: exec, open/openat/openat2/creat, chdir, TCP connect
           ┌───────▼────────┐  GET /activity                   ┌──────────────────┐
           │  yeet reader   │ ───────────────────────────────▶ │     sentinel     │
           │ (yeet service) │                                  │  owns cognee     │
           └────────────────┘                                  │  /ask /learn     │
                                                               │  /review, patrol │
                                                               └────────┬─────────┘
                                                                        │ profile (json) + findings
                                                                        │ recall (policy, profile, reviews)
                                                                        ▼
                                                            LLM (cognee's configured model)
                                                                        │
                                                                        ▼
                                                                 VERDICT: ...
```

The reader is a yeet service: one isolate that drains the BPF ring buffers
into a bounded in-memory log, identifies each process from its own exec, and
answers window queries. The sentinel is one Python process that owns cognee,
keeps a JSON profile per role for exact comparison and a prose copy in cognee
for the verdict, and computes findings before the model ever sees anything.
The agents are plain processes; nothing is installed in them.

## For coding agents

`skill/sentinel/SKILL.md` is a skill a coding agent (Claude Code, Cursor, and
others that read `SKILL.md`) can load to run this demo and ask the sentinel on
a user's behalf.

## 6. See what it remembers

Every run writes `.demo/memory-graph.html` — cognee's graph of the policy,
the learned profiles and your rulings, each tagged by kind (`policy`,
`profile`, `review`) so recall can scope to just the prior rulings when it
looks for precedent. Open it in a browser.
