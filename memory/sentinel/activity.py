"""Turn the activity log into prose: what a workload did, not just what it held.

The reader's `/activity` route returns every exec, open and connect the
watched processes (and their children) performed in a window. Most of it is
noise a process makes by existing -- loading shared libraries, reading its
own `/proc/self`, touching `/dev/null`. This module sorts the events into
the few sentences that matter for a judgement: which programs it ran, which
files it opened outside its own workspace, which hosts it dialled, and
whether any of that touched a host-revealing path. Facts only, no verdicts.
"""

from __future__ import annotations

import pathlib
import re
import socket
from collections import Counter
from dataclasses import dataclass, field

from .snapshot import describe_endpoint, human_bytes, is_sensitive, joined_cmdline, workload_name

# Paths every process touches just by running. Dropped from the prose.
NOISE_PREFIXES = (
    "/usr/lib", "/lib", "/lib64", "/usr/lib64", "/usr/share", "/usr/local/lib",
    "/etc/ld.so", "/proc/self/", "/proc/sys/", "/dev/null", "/dev/urandom", "/dev/random",
    "/dev/tty", "/dev/pts/", "/sys/fs/cgroup/", "/etc/localtime", "/etc/nsswitch.conf",
    "/etc/host.conf", "/etc/resolv.conf", "/etc/hosts", "/etc/gai.conf", "/etc/ssl/",
    "/etc/pki/", "/etc/crypto-policies/", "/usr/bin/", "/usr/sbin/", "/bin/", "/sbin/",
    "/etc/python", "/var/lib/ca-certificates/",
)
NOISE_PARTS = ("/site-packages/", "/__pycache__/", "/.pyenv/", "/.venv/", "/dist-packages/",
               "/node_modules/", "/.cache/", "/lib/python")
NOISE_SUFFIXES = (".so", ".pyc", ".dist-info", "pyvenv.cfg")


def is_noise(path: str) -> bool:
    if path.startswith(NOISE_PREFIXES):
        return True
    if any(part in path for part in NOISE_PARTS):
        return True
    return path.endswith(NOISE_SUFFIXES) or ".so." in path


# Where this host's agents are meant to work (see context/sandbox-policy.md).
WORKSPACE_ROOTS = (str(pathlib.Path.home() / "agent-workspace"),)


_PROC_PID = re.compile(r"^/proc/(\d+)(/|$)")


def generalize(path: str) -> str:
    """`/proc/4821/cmdline` -> `/proc/<pid>/cmdline`: twenty-four such opens
    are one fact (it read other processes' command lines), not twenty-four."""
    return _PROC_PID.sub(r"/proc/<pid>\2", path) if not path.startswith("/proc/1/") else path


def in_workspace(path: str, roots: tuple[str, ...]) -> bool:
    return any(root and (path == root or path.startswith(root.rstrip("/") + "/")) for root in roots)


@dataclass
class Activity:
    """One watched process's actions over a window, reduced to the facts."""
    pid: int
    name: str
    exe: str | None
    cmdline: str
    cwd: str | None
    events: int
    truncated: int
    programs: Counter = field(default_factory=Counter)       # exec'd filenames
    children: int = 0
    workspace_opens: Counter = field(default_factory=Counter)  # paths under cwd/workspace
    other_opens: Counter = field(default_factory=Counter)      # outside workspace, not noise
    sensitive_opens: Counter = field(default_factory=Counter)  # host-revealing paths
    writes: Counter = field(default_factory=Counter)           # paths opened for writing
    connects: Counter = field(default_factory=Counter)         # (addr, port)
    noise: int = 0

    @property
    def roots(self) -> tuple[str, ...]:
        return tuple(r for r in (self.cwd,) if r) + WORKSPACE_ROOTS


# Hostnames the policy approves, resolved once so a bare IP in the log can be
# named. DNS answers are facts at observation time, not interpretation.
KNOWN_HOSTS = ("api.github.com", "api.anthropic.com", "github.com")
_resolved: dict[str, str] | None = None


def name_for(addr: str) -> str | None:
    global _resolved
    if _resolved is None:
        _resolved = {}
        for host in KNOWN_HOSTS:
            try:
                for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
                    _resolved.setdefault(info[4][0], host)
            except OSError:
                pass
    if addr not in _resolved:
        # A host like github.com answers from many addresses; the reverse name
        # (lb-140-82-116-5-iad.github.com) says whose it is.
        try:
            _resolved[addr] = socket.gethostbyaddr(addr)[0]
        except OSError:
            _resolved[addr] = None
    return _resolved.get(addr)


def endpoint(addr: str, port: int) -> str:
    host = name_for(addr)
    base = describe_endpoint(addr, port)
    return f"{host} ({base}) port {port}" if host else f"{base} port {port}"


def summarize(group: dict, workspace_roots: tuple[str, ...] = ()) -> Activity:
    act = Activity(
        pid=group["pid"], name=workload_name(group.get("comm"), group.get("cmdline")), exe=group.get("exe"),
        cmdline=joined_cmdline(group.get("cmdline")), cwd=group.get("cwd"),
        events=len(group.get("events") or []), truncated=int(group.get("truncated") or 0),
        children=len(group.get("children") or []),
    )
    roots = act.roots + tuple(workspace_roots)
    for e in group.get("events") or []:
        kind = e.get("kind")
        if kind == "exec":
            act.programs[e.get("filename") or "?"] += 1
        elif kind == "connect":
            act.connects[(e.get("addr"), int(e.get("port") or 0))] += 1
        elif kind == "open":
            path = e.get("path") or ""
            if e.get("access") in ("write", "read-write") or e.get("create"):
                if not is_noise(path):
                    act.writes[path] += 1
            if is_sensitive(path):
                act.sensitive_opens[path] += 1
            elif in_workspace(path, roots):
                act.workspace_opens[path] += 1
            elif is_noise(path):
                act.noise += 1
            else:
                act.other_opens[generalize(path)] += 1
    return act


def _listed(counter: Counter, limit: int = 12, fmt=lambda k: f"`{k}`") -> str:
    items = counter.most_common(limit)
    text = ", ".join(f"{fmt(k)}" + (f" ({n} times)" if n > 1 else "") for k, n in items)
    rest = len(counter) - len(items)
    return text + (f", and {rest} more" if rest > 0 else "")


def render_activity(host: str, label: str, act: Activity, window: str) -> list[str]:
    """Prose for one process's activity. `window` names the period in words."""
    name, pid = act.name, act.pid
    out: list[str] = []
    out.append("")
    out.append(f"## What `{name}` (pid {pid}) did during {window}")
    out.append("")
    out.append(
        f"During run `{label}` on host {host}, the kernel recorded {act.events} actions "
        f"by workload `{name}` (pid {pid}, `{act.cmdline or act.exe}`) and its "
        f"{act.children} child processes"
        + (f"; {act.truncated} more were not kept" if act.truncated else "")
        + ". This is the activity log, which sees every action as it happens, "
        f"including ones over before any reading was taken."
    )

    out.append("")
    if act.programs:
        out.append(
            f"Workload `{name}` (pid {pid}) on {host} ran these programs: "
            + _listed(act.programs) + "."
        )
    else:
        out.append(f"Workload `{name}` (pid {pid}) on {host} ran no other programs.")

    out.append("")
    if act.workspace_opens:
        out.append(
            f"Inside its own working directory, workload `{name}` (pid {pid}) on {host} "
            f"opened {len(act.workspace_opens)} distinct files, for example "
            + _listed(act.workspace_opens, 6) + "."
        )
    else:
        out.append(
            f"Workload `{name}` (pid {pid}) on {host} opened no files inside its own "
            f"working directory."
        )

    out.append("")
    if act.other_opens:
        out.append(
            f"Outside its working directory, workload `{name}` (pid {pid}) on {host} "
            f"opened these files: " + _listed(act.other_opens) + "."
        )
    else:
        out.append(
            f"Workload `{name}` (pid {pid}) on {host} opened nothing outside its working "
            f"directory, apart from the system libraries every process loads."
        )

    peeked = sum(n for p, n in act.other_opens.items() if p.startswith("/proc/<pid>/"))
    if peeked:
        out.append("")
        out.append(
            f"Workload `{name}` (pid {pid}) on {host} read entries under other processes' "
            f"/proc directories {peeked} times ({', '.join(sorted({p for p in act.other_opens if p.startswith('/proc/<pid>/')}))}). "
            f"Those files describe other processes on the host, not this workload's own work."
        )

    out.append("")
    if act.sensitive_opens:
        out.append(
            f"Workload `{name}` (pid {pid}) on {host} attempted to open these privileged or "
            f"host-revealing paths: " + _listed(act.sensitive_opens) + ". An attempt is "
            f"recorded whether or not the kernel allowed it; these paths are outside an "
            f"ordinary workload's working directory and grant visibility into or control "
            f"over the host."
        )
    else:
        out.append(
            f"Workload `{name}` (pid {pid}) on {host} attempted to open no privileged or "
            f"host-revealing paths."
        )

    if act.writes:
        out.append("")
        out.append(
            f"Workload `{name}` (pid {pid}) on {host} opened these paths for writing: "
            + _listed(act.writes) + "."
        )

    out.append("")
    if act.connects:
        out.append(
            f"Workload `{name}` (pid {pid}) on {host} made outbound connections to: "
            + _listed(act.connects, 10, lambda k: endpoint(k[0], k[1]))
            + "."
        )
    else:
        out.append(f"Workload `{name}` (pid {pid}) on {host} made no outbound connections.")

    return out


def render_compact(host: str, label: str, act: Activity, held: dict | None = None) -> list[str]:
    """One paragraph per workload: identity, what it held, what it did. Used
    when a run covers many workloads, where the full renderer's prose would
    run to tens of thousands of characters and cognify for a long time."""
    name, pid = act.name, act.pid
    status = (held or {}).get("status") or {}
    bits = [
        f"During run `{label}` on host {host}, workload `{name}` (pid {pid}, process "
        f"`{status.get('name') or act.name}`, uid {(held or {}).get('uid', '?')}, command line "
        f"`{act.cmdline or act.exe}`) worked in `{act.cwd}`."
    ]
    if held:
        rss = (status.get("vmrss") or 0) * 1024
        bits.append(f"It held {held.get('fd_count', '?')} open file descriptors and used {human_bytes(rss)} of memory.")
    bits.append(
        f"The kernel recorded {act.events} actions by it and its {act.children} child processes: "
        + (f"it ran {_listed(act.programs, 6)}; " if act.programs else "it ran no other programs; ")
        + (f"it opened {len(act.workspace_opens)} distinct files inside its own workspace; "
           if act.workspace_opens else "it opened no files inside its own workspace; ")
        + (f"outside its workspace it opened {_listed(act.other_opens, 8)}; "
           if act.other_opens else "it opened nothing outside its workspace apart from system libraries; ")
        + (f"it READ OTHER PROCESSES' /proc ENTRIES {sum(n for p, n in act.other_opens.items() if p.startswith('/proc/<pid>/'))} times, "
           f"which describe other processes on the host rather than its own work; "
           if any(p.startswith("/proc/<pid>/") for p in act.other_opens) else "")
        + (f"it ATTEMPTED TO OPEN the privileged or host-revealing paths {_listed(act.sensitive_opens, 8)}, "
           f"which is recorded whether or not the kernel allowed it; "
           if act.sensitive_opens else "it attempted no privileged or host-revealing paths; ")
        + (f"it made outbound connections to {_listed(act.connects, 8, lambda k: endpoint(k[0], k[1]))}."
           if act.connects else "it made no outbound connections.")
    )
    return ["", f"## Workload `{name}` during run `{label}`", "", " ".join(bits)]


def summarize_all(payload: dict | None, workspace_roots: tuple[str, ...] = ()) -> list[Activity]:
    if not payload:
        return []
    return [summarize(g, workspace_roots) for g in payload.get("processes") or []]
