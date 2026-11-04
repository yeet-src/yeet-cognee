"""Turn a live yeet sysgraph reading into prose cognee can actually remember.

The shape of this file is the whole trick. Handing cognee raw JSON gives it
nothing to link: `{"pid":1420,"fds":[...]}` has no entities. Rendering the
same reading as English with stable entity names -- host, process, binary,
path, remote endpoint -- lets cognee resolve those entities across hundreds
of snapshots, which is what makes "is this normal for this box?" answerable.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field

from .yeetq import Yeet

# The GraphQL that produces a reading now lives in the yeet service
# (../yeet-service/lib/queries.js): the service runs the queries, matches the
# watched processes, attributes their sockets, and returns one assembled JSON
# reading. This module consumes that reading and renders it as prose, so no
# GraphQL is issued from here.

# Processes that are part of the sentinel itself -- excluded from watch
# matching so the collector and ask tools never observe themselves.
SELF_MARKERS = ("sentinel.collector", "sentinel.ask", "sentinel.seed", "-m sentinel")

# --------------------------------------------------------------------------
# Interpretation helpers -- facts only, no verdicts
# --------------------------------------------------------------------------

# Paths that are capability-granting or host-revealing from inside a sandbox.
# Flagging them is not a verdict; it just makes sure the fact survives into
# prose so cognee has a chance of linking it later.
SENSITIVE_PREFIXES = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/var/run/containerd",
    "/run/containerd",
    "/var/run/crio",
    "/dev/kmsg",
    "/dev/mem",
    "/dev/kvm",
    "/sys/kernel",
    "/sys/fs/cgroup",
    "/etc/shadow",
    "/etc/sudoers",
    "/etc/passwd",       # host user list -- reconnaissance from a sandbox
    "/root/",
    "/proc/1/",          # the init process -- inspecting the host
    "/proc/mounts",      # the host mount table -- looking for a way out
    "/proc/sys/kernel",
)

SENSITIVE_SUFFIXES = ("/environ", "/mem", "/maps", "id_rsa", "id_ed25519", ".kube/config")

WELL_KNOWN_PORTS = {
    22: "SSH", 53: "DNS", 80: "HTTP", 123: "NTP", 443: "HTTPS",
    2375: "Docker API (plaintext)", 2376: "Docker API (TLS)",
    3306: "MySQL", 5432: "PostgreSQL", 6379: "Redis",
    8080: "HTTP-alt", 11434: "Ollama",
}


def is_sensitive(path: str | None) -> bool:
    if not path:
        return False
    if path.startswith(SENSITIVE_PREFIXES):
        return True
    return any(path.endswith(s) for s in SENSITIVE_SUFFIXES)


def describe_endpoint(addr: str, port: int) -> str:
    name = WELL_KNOWN_PORTS.get(port)
    return f"{addr} ({name})" if name else addr


def kb_to_bytes(kb: int | float | None) -> int | None:
    """`/proc/<pid>/status` reports VmRSS and friends in kB; the sysgraph
    carries them through unchanged, so convert to bytes before formatting."""
    return None if kb is None else int(kb) * 1024


def human_bytes(n: int | float | None) -> str:
    if n is None:
        return "unknown"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} days, {hours} hours"
    if hours:
        return f"{hours} hours, {minutes} minutes"
    return f"{minutes} minutes"


def joined_cmdline(cmdline: list[str] | None) -> str:
    return " ".join(cmdline) if cmdline else ""


# --------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------

@dataclass
class Snapshot:
    hostname: str
    taken_at: dt.datetime
    run_label: str
    system: dict
    watched: list[dict] = field(default_factory=list)
    other_procs: list[dict] = field(default_factory=list)

    @property
    def stamp(self) -> str:
        return self.taken_at.isoformat(timespec="seconds")


def _parse_ts(value: str | None) -> dt.datetime:
    """The service stamps each reading with an ISO-8601 time; fall back to now."""
    if value:
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
        except ValueError:
            pass
    return dt.datetime.now().astimezone()


def take(
    yeet: Yeet,
    watch: list[str],
    run_label: str,
    hostname: str | None = None,
    top_n_other: int = 5,
) -> Snapshot:
    """Fetch one assembled reading from the yeet sysgraph service.

    The service does the work that used to live here -- running the queries,
    matching the watched processes, attributing each one's sockets by inode,
    and ranking the background processes. This just wraps the JSON it returns
    in a Snapshot for the prose renderer.
    """
    payload = yeet.reading(
        watch,
        self_markers=list(SELF_MARKERS),
        top_other=top_n_other,
    )
    return Snapshot(
        hostname=hostname or payload.get("hostname") or "unknown-host",
        taken_at=_parse_ts(payload.get("taken_at")),
        run_label=run_label,
        system=payload.get("system") or {},
        watched=payload.get("watched") or [],
        other_procs=payload.get("other_procs") or [],
    )


# --------------------------------------------------------------------------
# Prose renderer
# --------------------------------------------------------------------------

def render(snap: Snapshot) -> str:
    """Render a snapshot as English. This is what goes into cognee."""
    lines: list[str] = []
    host = snap.hostname

    lines.append(f"# System observation of host {host} at {snap.stamp}")
    lines.append("")
    lines.append(
        f"This is a recording of the live state of host {host}, taken from the "
        f"yeet system graph at {snap.stamp}. It belongs to the observation run "
        f"labelled `{snap.run_label}`."
    )
    lines.append("")

    # ---- system ----
    load = snap.system.get("load_average") or {}
    def _n(v):
        return f"{v:.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else v
    mem = snap.system.get("meminfo") or {}
    uptime = ((snap.system.get("host") or {}).get("uptime") or {}).get("uptime")
    used = (mem.get("mem_total", 0) or 0) - (mem.get("mem_available", 0) or 0)

    lines.append("## Overall system state")
    lines.append("")
    lines.append(
        f"Host {host} had a load average of {_n(load.get('one', '?'))} over one minute, "
        f"{_n(load.get('five', '?'))} over five minutes and {_n(load.get('fifteen', '?'))} "
        f"over fifteen minutes. It was using {human_bytes(used)} of "
        f"{human_bytes(mem.get('mem_total'))} of memory. It had been up for "
        f"{human_duration(uptime)}. There were {load.get('cur', '?')} runnable "
        f"tasks out of {load.get('max', '?')} total."
    )

    for dev in ((snap.system.get("nvidia") or {}) or {}).get("devices", []) or []:
        util = dev.get("utilization_rates") or {}
        gmem = dev.get("memory") or {}
        procs_on_gpu = dev.get("running_compute_processes") or []
        sentence = (
            f"GPU {dev.get('index')} on {host} is an {dev.get('name')}. It was at "
            f"{util.get('gpu', '?')} percent compute utilization and "
            f"{util.get('memory', '?')} percent memory utilization, using "
            f"{human_bytes(gmem.get('used'))} of {human_bytes(gmem.get('total'))} "
            f"of GPU memory."
        )
        if procs_on_gpu:
            pids = ", ".join(
                f"pid {p['pid']} holding {human_bytes(p.get('used_gpu_memory'))}"
                for p in procs_on_gpu
            )
            sentence += f" Compute processes on this GPU: {pids}."
        else:
            sentence += " No compute processes were running on it."
        lines.append("")
        lines.append(sentence)

    # ---- watched processes ----
    lines.append("")
    if not snap.watched:
        lines.append("## Watched workloads")
        lines.append("")
        lines.append(
            f"No watched workload was running on {host} at {snap.stamp}."
        )
    for proc in snap.watched:
        lines.extend(_render_proc(proc, host, snap.stamp))

    # ---- background ----
    if snap.other_procs:
        lines.append("")
        lines.append("## Other processes on the host")
        lines.append("")
        lines.append(
            f"The largest other processes on {host} by resident memory were: "
            + "; ".join(
                f"`{(p.get('status') or {}).get('name')}` (pid {p['pid']}, uid "
                f"{p.get('uid')}, {human_bytes(kb_to_bytes((p.get('status') or {}).get('vmrss')))} resident)"
                for p in snap.other_procs
            )
            + "."
        )

    lines.append("")
    return "\n".join(lines)


def _render_proc(proc: dict, host: str, stamp: str) -> list[str]:
    status = proc.get("status") or {}
    name = status.get("name") or "unknown"
    pid = proc.get("pid")
    cmd = joined_cmdline(proc.get("cmdline"))

    out: list[str] = []
    out.append("")
    out.append(f"## Watched workload `{name}` (pid {pid})")
    out.append("")
    out.append(
        f"At {stamp} on host {host}, the process `{name}` was running as pid {pid} "
        f"under uid {proc.get('uid')}, with parent pid {status.get('ppid')}. Its "
        f"executable is `{proc.get('exe')}`. It was launched with the command line "
        f"`{cmd}`. Its working directory is `{proc.get('cwd')}`. It held "
        f"{proc.get('fd_count')} open file descriptors, used "
        f"{human_bytes(kb_to_bytes(status.get('vmrss')))} of resident memory and ran "
        f"{status.get('threads')} threads."
    )

    io = proc.get("io") or {}
    if io:
        out.append("")
        out.append(
            f"Process `{name}` (pid {pid}) on {host} had read "
            f"{human_bytes(io.get('read_bytes'))} from disk across "
            f"{io.get('syscr')} read syscalls, and written "
            f"{human_bytes(io.get('write_bytes'))} across {io.get('syscw')} "
            f"write syscalls."
        )

    # ---- file descriptors ----
    fds = proc.get("fds") or []
    paths = sorted({fd["path"] for fd in fds if fd.get("kind") == "PATH" and fd.get("path")})
    sensitive = [p for p in paths if is_sensitive(p)]
    ordinary = [p for p in paths if not is_sensitive(p)]

    out.append("")
    if ordinary:
        out.append(
            f"The files process `{name}` (pid {pid}) on {host} had open were: "
            + ", ".join(f"`{p}`" for p in ordinary)
            + "."
        )
    else:
        out.append(
            f"Process `{name}` (pid {pid}) on {host} had no ordinary files open."
        )

    if sensitive:
        out.append("")
        out.append(
            f"Process `{name}` (pid {pid}) on {host} held open file descriptors on "
            f"these privileged or host-revealing paths: "
            + ", ".join(f"`{p}`" for p in sensitive)
            + ". These paths are outside an ordinary workload's own working "
            f"directory and grant visibility into or control over the host."
        )

    # ---- network ----
    out.append("")
    conns = [
        s for s in (proc.get("sockets") or [])
        if (s.get("remote_address") or {}).get("port")
    ]
    established = [s for s in conns if s.get("state") == "Established"]
    listening = [s for s in (proc.get("sockets") or []) if s.get("state") == "Listen"]

    if established:
        out.append(
            f"Process `{name}` (pid {pid}) on {host} had outbound network "
            f"connections to: "
            + ", ".join(
                describe_endpoint(s["remote_address"]["addr"], s["remote_address"]["port"])
                for s in established
            )
            + "."
        )
    else:
        out.append(
            f"Process `{name}` (pid {pid}) on {host} had no established outbound "
            f"network connections."
        )
    if listening:
        out.append("")
        out.append(
            f"Process `{name}` (pid {pid}) on {host} was listening on: "
            + ", ".join(s["local_address"]["addr"] for s in listening)
            + "."
        )

    # ---- mounts ----
    mounts = proc.get("mounts") or []
    interesting = [
        m for m in mounts
        if m.get("fs_type") not in {"proc", "sysfs", "devpts", "cgroup2", "mqueue",
                                     "securityfs", "debugfs", "tracefs", "pstore",
                                     "bpf", "configfs", "fusectl", "hugetlbfs",
                                     "autofs", "ramfs", "selinuxfs", "efivarfs",
                                     "binfmt_misc", "nsfs"}
    ]
    if interesting:
        out.append("")
        out.append(
            f"The filesystems visible in the mount namespace of process `{name}` "
            f"(pid {pid}) on {host} were: "
            + ", ".join(
                f"`{m.get('mount_point')}` ({m.get('fs_type')} from "
                f"`{m.get('source')}`)" for m in interesting[:12]
            )
            + "."
        )

    return out


def render_json(snap: Snapshot) -> str:
    """Raw reading, kept alongside the prose for the demo's before/after."""
    return json.dumps(
        {
            "hostname": snap.hostname,
            "taken_at": snap.stamp,
            "run_label": snap.run_label,
            "system": snap.system,
            "watched": snap.watched,
        },
        indent=2,
        default=str,
    )
