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
2. The sentinel seeding the policy into cognee. This is the slow part on a
   local model.
3. A clean fleet of five agents working for 45 seconds, then `learn`: the
   `agent` profile is written from what they did.
4. The real fleet: `agent-04` in `policy` mode, `agent-05` in `suspicious`
   mode, the rest well behaved.
5. One verdict, with the findings it was computed from, and one review.

## 3. Read the verdict

```
VERDICT: YES
agent-04 attempted to open `/etc/shadow` and `/var/run/docker.sock`, which are
explicitly forbidden by the policy. agent-05 read other processes' /proc entries
75 times, which is reconnaissance and suspicious according to the profile.
```

The `VERDICT:` line is a contract a script can branch on. The sentences cite
paths, endpoints and programs from the activity, never from the policy's
examples. The findings behind it are in the `/ask` response and in
`memory/verdicts.jsonl`:

```
[policy] agent-04: attempted to open `/etc/shadow`
[novel]  agent-04: dialled `one.one.one.one:443`, which no `agent` workload has dialled before
[novel]  agent-05: read other processes' /proc entries 75 times; no `agent` workload has done that before
```

A `policy` finding breaks a written rule or tampers with a program. A `novel`
finding is outside the learned profile and is suspicious until reviewed.

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

## 6. Point it at your own workloads

- Pass your agents' process name or a `--name` prefix as `--watch`. A
  workload's role is its name with any trailing number stripped, so
  `builder-01` and `builder-02` share the `builder` profile.
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
