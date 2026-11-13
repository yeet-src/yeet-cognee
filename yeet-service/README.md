# yeet-service — the reader

A yeet service that serves the kernel's view of this host over HTTP.

```bash
yeet service apply -f service.toml        # from this directory
curl -s 'http://127.0.0.1:9490/activity?watch=agent-&since=0' | jq .
```

## Routes

- `GET /activity?watch=a,b&since=<ms>&until=<ms>&limit=N` — every exec, open
  and connect by the watched processes and their children in the window.
- `GET /reading?watch=a,b&top=N` — what the watched processes hold right now
  (fds, sockets, memory) plus whole-box load, memory and GPU.

`watch` matches a process when any of its exe, comm or argv tokens equals the
needle or has a basename starting with it: `agent-` matches `--name agent-04`,
`python` matches `/usr/bin/python3.13`, a URL containing `agent-` does not.
`self` adds markers to exclude; the sentinel's own processes are excluded by
default.

## The activity log

`bpf/activity.bpf.c` attaches tracepoints for exec, the four open syscalls,
chdir and TCP state change. `lib/activity.js` drains their ring buffers into
one bounded in-memory ring (`--log-capacity`, default 500k events), learns
each process's identity from its exec, tracks its cwd so relative paths
resolve, filters opens every process makes just by running, and marks writes
to programs the host has executed. The response's `log` block reports the
ring's size, overwritten events, filtered noise, and kernel-side drops per
stream.

The object `activity.bpf.o` is CO-RE and checked in. After editing the C,
`make` rebuilds it (needs clang and bpftool).
