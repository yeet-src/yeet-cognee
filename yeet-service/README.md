# yeet-service — the sysgraph reader

This is the **yeet side** of the sentinel: a yeet service that reads the
system graph and serves one complete host reading as JSON over HTTP, and
keeps an activity log of what processes do, from three BPF tracepoints. It
is ground truth and nothing more — no interpretation happens here.

## What it exposes

```
GET /                         a complete host reading, JSON
GET /reading                  same thing (named alias)
GET /reading?watch=agent-04   also pull that workload in full (fds, io, sockets)
GET /reading?watch=a,b&top=8  several workloads; more background processes
GET /activity?watch=agent-&since=<epoch ms>[&until=<ms>&limit=N]
                              what those workloads did since then: every exec,
                              open (with access mode) and TCP connect, children
                              folded into the parent that spawned them
```

The activity log comes from `bpf/activity.bpf.c`: `sched_process_exec`,
`sys_enter_openat` and `inet_sock_set_state` (SYN_SENT) write into ring
buffers the isolate drains into a bounded in-memory log (`--log-capacity`,
default 500k events; opens of shared libraries and other per-process noise
are dropped at ingest). An open is recorded whether or not the kernel allowed
it. The compiled `activity.bpf.o` is CO-RE and checked in; `make` rebuilds it
(needs clang and bpftool).

A reading is:

- **whole-box state** — load average, memory, GPU (from `SYSTEM`)
- **watched workloads in full** — every process whose exe or cmdline contains
  a `watch` substring, with its open file descriptors (and their paths), io
  counters, status, and its sockets **attributed by inode**
- **top background processes** — the largest others by resident memory

Query parameters:

| param | meaning | default |
|---|---|---|
| `watch` | comma list of substrings to match against each process's exe + cmdline | none |
| `self` | extra substrings to exclude from watch matching (the reader already excludes the sentinel's own tools) | none |
| `top` | how many background processes to include, ranked by RSS | 5 |
| `hostname` | override the stamped host name | the service's `--hostname` |

## Files

| File | What it does |
|---|---|
| `service.toml` | the service manifest: one `serve` unit, a web server, three routes |
| `reading.js` | the HTTP portal — parses the request, calls `reading()` or `activity()`, writes JSON |
| `lib/activity.js` | the activity log: loads the BPF object, drains the ring buffers, attributes events to watched processes |
| `bpf/activity.bpf.c`, `activity.bpf.o`, `Makefile` | the three tracepoint programs and their build |
| `lib/queries.js` | the GraphQL run against `yeet.graph` (host, procs, tcp, per-proc detail) |
| `lib/assemble.js` | runs the queries, matches watched procs, attributes sockets, ranks the rest |

The GraphQL is read-only. A process that exits between the process listing and
its detail query is tolerated (a path error), not a failure.

## Running it

Requires a Linux host with yeet installed and `yeetd` running.

```bash
# apply it (creates or updates the service, rolls out the change)
yeet service apply -f service.toml

# confirm it's up
yeet service tree | grep -A4 sentinel-sysgraph

# verify it's working
curl -s http://127.0.0.1:9490/ | python3 -m json.tool | head -40
```

By default each reading is stamped with the machine's own name (the cognee side
fills it in), which is all a single-box demo needs. To stamp a specific name —
e.g. when the cognee side runs on another host, so readings link to the right
box — set it in `service.toml`: `args = ["--hostname", "my-box"]`.

To focus on a workload, launch something and watch it:

```bash
curl -s "http://127.0.0.1:9490/reading?watch=my_workload" | python3 -m json.tool
```

### Notes

- **Port.** Defaults to `9490`. If that's taken (`yeet service tree` shows the
  conflict), change the `web-server` line in `service.toml`.
- **Binding.** Bound to `127.0.0.1` on purpose — a reading exposes host
  internals. If the cognee side runs on another box, change it to `0.0.0.0`
  and firewall the port.
- **Privilege.** The reader runs as part of `yeetd`, so it sees every
  process's fds and sockets regardless of the uid that owns them.
- **Updating.** Edit a file and re-run `yeet service apply -f service.toml`;
  the change rolls out in place. `yeet service stop sentinel-sysgraph` takes it
  down; `yeet service remove sentinel-sysgraph` deletes it.
