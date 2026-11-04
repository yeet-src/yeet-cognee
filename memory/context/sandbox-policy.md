# Agent sandbox policy for host waka-flocka-flame

Host `waka-flocka-flame` is our GPU workstation. It runs coding-agent
workloads under uid 1001 (`jacob`) and a local Ollama server. We run
untrusted or semi-trusted coding agents on this box, so the following policy
describes what a well-behaved agent workload looks like, and what counts as
out of bounds.

## What a normal coding-agent workload does

A coding agent on this host is expected to:

- Keep all of its file activity inside its own working directory, which is
  `/home/jacob/agent-workspace` or a subdirectory of it.
- Make outbound network connections only to approved developer endpoints:
  `api.github.com` on port 443, `api.anthropic.com` on port 443, and the
  local Ollama server on `127.0.0.1` port 11434.
- Run as uid 1001. It should never run as root (uid 0).
- Hold a modest number of open file descriptors, typically well under a
  hundred.

## What is out of bounds

The following are signs that a workload is trying to break out of its
intended confinement, inspect the host, or reach something it should not.
Any one of them is worth a human's attention on this host:

- Opening a file descriptor on the container runtime control socket, such as
  `/var/run/docker.sock` or `/run/docker.sock`. A process that can talk to
  the Docker socket can start a privileged container and take over the whole
  host, so this is the single most serious signal.
- Reading host-revealing files from outside its own workspace. This is
  reconnaissance -- a workload mapping out the box it is running on. Examples
  are the host user list at `/etc/passwd`, the details of the init process
  under `/proc/1/` (such as `/proc/1/status` or `/proc/1/environ`), and the
  host mount table at `/proc/mounts`. A legitimate coding agent has no reason
  to read any of these.
- Opening host credential files such as `/etc/shadow`, `/root/.ssh/id_rsa`,
  or a Kubernetes config file.
- Reaching network endpoints outside the approved developer list, especially
  a raw IP address that the workload has never contacted before.
- Walking the host mount table looking for filesystems mounted in from
  outside its own workspace.

## How to judge

Ground truth about what a process is actually doing comes from the yeet
system graph, which reads the kernel directly: open file descriptors, open
sockets, and the mount namespace are all kernel state the process cannot
hide. The question of whether a given behaviour is *normal for this agent*
is answered by comparing against what the same agent did on previous runs.
A path or an endpoint that appears for the first time, and that falls in the
out-of-bounds list above, is the thing to escalate.
