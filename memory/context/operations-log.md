# Operations log and runbook for host waka-flocka-flame

This is the running operational knowledge for `waka-flocka-flame`, our GPU
workstation. It is the kind of context a new on-call engineer would need to
tell a real problem apart from business as usual.

## Normal rhythms of this box

- `waka-flocka-flame` is a single RTX 5070 workstation with 32 GB of RAM. An
  idle load average on this host is around 0.0 to 0.5. A load average above
  about 4.0 means something heavy is running.
- The Ollama server (`ollama`) is normally resident and holds a few hundred
  megabytes to a few gigabytes depending on which model is loaded. GPU
  utilisation sits near 0 percent when no inference is in flight and spikes
  to 80-100 percent during inference. A GPU spike by itself is normal; it
  just means a model is being served.
- Model loads are the most common cause of a sudden memory and GPU jump.
  Loading `qwen3:8b` pulls roughly 5 GB onto the GPU. That is expected, not a
  leak.

## Deploys

- We deploy services to this host with `deploy.sh`. A deploy restarts
  containers, so during a deploy you will see containers stop and start,
  short-lived spikes in load average, and brief bursts of disk I/O. This is
  normal and self-resolves within a minute or two. A load spike that lines up
  in time with a deploy is almost always the deploy, not an incident.
- `yeetd`, the yeet daemon, is restarted as part of some deploys. A brief gap
  in yeetd availability around a deploy window is expected.

## Known-good workloads

- The Ollama server listening on `127.0.0.1:11434` is expected and normal.
- Coding-agent runs under `/home/jacob/agent-workspace` talking to
  `api.github.com` and `api.anthropic.com` are the routine workload this box
  exists to run.

## When to escalate

- A sustained high load average (above ~4.0 for several minutes) that does
  **not** line up with a deploy or a known model load.
- Any workload touching the paths called out as out of bounds in the sandbox
  policy. That is a security question, not a capacity question, and goes to a
  human immediately regardless of load.
- A process running as root (uid 0) that is not a known system daemon.
