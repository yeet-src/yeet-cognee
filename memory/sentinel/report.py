"""Fold a run's worth of snapshots into one prose document.

One document per run, rather than one per sample, for two reasons. It is
the right unit of memory -- "what does this agent normally do" is a question
about runs, not instants -- and it keeps the number of cognify() passes
(each one a local LLM call) small enough to demo live.
"""

from __future__ import annotations

from .snapshot import (
    Snapshot,
    describe_endpoint,
    human_bytes,
    is_sensitive,
    joined_cmdline,
)


def _num(v) -> str:
    """Render a number without float noise (0.0199999 -> 0.02)."""
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


def _fmt_range(values: list[float], fmt=_num) -> str:
    if not values:
        return "unknown"
    lo, hi = min(values), max(values)
    return fmt(lo) if lo == hi else f"{fmt(lo)} to {fmt(hi)}"


def render_run_report(snaps: list[Snapshot]) -> str:
    """Render the union of everything observed during one run, as English."""
    if not snaps:
        return ""

    host = snaps[0].hostname
    label = snaps[0].run_label
    start, end = snaps[0].stamp, snaps[-1].stamp

    lines: list[str] = []
    lines.append(f"# Run `{label}` on host {host}")
    lines.append("")
    lines.append(
        f"This is a record of the observation run labelled `{label}`, which took "
        f"place on host {host} between {start} and {end}. It was assembled from "
        f"{len(snaps)} readings of the yeet system graph, which observes the "
        f"kernel directly. Everything stated here is measured, not inferred."
    )

    # ---- host conditions during the run ----
    loads = [s.system.get("load_average", {}).get("one") for s in snaps]
    loads = [v for v in loads if v is not None]
    used = []
    for s in snaps:
        mem = s.system.get("meminfo") or {}
        if mem.get("mem_total"):
            used.append(mem["mem_total"] - mem.get("mem_available", 0))

    lines.append("")
    lines.append("## Host conditions during the run")
    lines.append("")
    lines.append(
        f"During run `{label}`, host {host} had a one-minute load average of "
        f"{_fmt_range(loads)} and was using {_fmt_range(used, human_bytes)} of "
        f"memory."
    )

    gpu_utils = []
    gpu_name = None
    for s in snaps:
        for dev in ((s.system.get("nvidia") or {}) or {}).get("devices", []) or []:
            gpu_name = dev.get("name")
            util = (dev.get("utilization_rates") or {}).get("gpu")
            if util is not None:
                gpu_utils.append(util)
    if gpu_utils:
        lines.append("")
        lines.append(
            f"The GPU on {host}, an {gpu_name}, was at {_fmt_range(gpu_utils)} "
            f"percent compute utilization during run `{label}`."
        )

    # ---- the watched workload ----
    by_pid: dict[int, list[dict]] = {}
    for s in snaps:
        for proc in s.watched:
            by_pid.setdefault(proc["pid"], []).append(proc)

    if not by_pid:
        lines.append("")
        lines.append(
            f"No watched workload was observed on {host} during run `{label}`."
        )
        lines.append("")
        return "\n".join(lines)

    for pid, observations in by_pid.items():
        lines.extend(_render_workload(host, label, pid, observations, len(snaps)))

    lines.append("")
    return "\n".join(lines)


def _render_workload(
    host: str, label: str, pid: int, obs: list[dict], total_samples: int
) -> list[str]:
    first = obs[0]
    status = first.get("status") or {}
    name = status.get("name") or "unknown"
    cmd = joined_cmdline(first.get("cmdline"))

    out: list[str] = []
    out.append("")
    out.append(f"## Workload `{name}` during run `{label}`")
    out.append("")
    out.append(
        f"During run `{label}` on host {host}, the process `{name}` ran as pid "
        f"{pid} under uid {first.get('uid')}. Its executable was "
        f"`{first.get('exe')}`. It was launched with the command line `{cmd}`, "
        f"and its working directory was `{first.get('cwd')}`. It was observed in "
        f"{len(obs)} of the {total_samples} readings taken during the run."
    )

    rss = [(o.get("status") or {}).get("vmrss") for o in obs]
    rss = [v * 1024 for v in rss if v]  # VmRSS is reported in kB
    fdc = [o.get("fd_count") for o in obs if o.get("fd_count") is not None]
    out.append("")
    out.append(
        f"Across run `{label}`, process `{name}` on {host} used "
        f"{_fmt_range(rss, human_bytes)} of resident memory and held "
        f"{_fmt_range(fdc, lambda v: str(int(v)))} open file descriptors."
    )

    # ---- union of every path touched ----
    all_paths: set[str] = set()
    for o in obs:
        for fd in o.get("fds") or []:
            if fd.get("kind") == "PATH" and fd.get("path"):
                all_paths.add(fd["path"])

    sensitive = sorted(p for p in all_paths if is_sensitive(p))
    ordinary = sorted(p for p in all_paths if not is_sensitive(p))

    out.append("")
    if ordinary:
        out.append(
            f"Over the whole of run `{label}`, process `{name}` on {host} opened "
            f"these files: " + ", ".join(f"`{p}`" for p in ordinary) + "."
        )
    else:
        out.append(
            f"Over the whole of run `{label}`, process `{name}` on {host} opened "
            f"no ordinary files."
        )

    out.append("")
    if sensitive:
        out.append(
            f"Over the whole of run `{label}`, process `{name}` on {host} also "
            f"opened these privileged or host-revealing paths: "
            + ", ".join(f"`{p}`" for p in sensitive)
            + ". These are outside the workload's own working directory and "
            f"grant visibility into, or control over, the host itself."
        )
    else:
        out.append(
            f"Over the whole of run `{label}`, process `{name}` on {host} opened "
            f"no privileged or host-revealing paths. It stayed inside its own "
            f"working directory."
        )

    # ---- union of every endpoint dialled ----
    endpoints: set[tuple[str, int]] = set()
    for o in obs:
        for sock in o.get("sockets") or []:
            remote = sock.get("remote_address") or {}
            if remote.get("port") and sock.get("state") == "Established":
                endpoints.add((remote["addr"], remote["port"]))

    out.append("")
    if endpoints:
        out.append(
            f"Over the whole of run `{label}`, process `{name}` on {host} made "
            f"outbound network connections to: "
            + ", ".join(describe_endpoint(a, p) for a, p in sorted(endpoints))
            + "."
        )
    else:
        out.append(
            f"Over the whole of run `{label}`, process `{name}` on {host} made no "
            f"outbound network connections."
        )

    # ---- mount namespace ----
    mount_points: set[str] = set()
    for o in obs:
        for m in o.get("mounts") or []:
            if m.get("fs_type") in {"ext4", "btrfs", "xfs", "overlay", "nfs", "9p",
                                     "virtiofs", "tmpfs", "zfs"}:
                mount_points.add(f"{m.get('mount_point')} ({m.get('fs_type')})")
    if mount_points:
        out.append("")
        out.append(
            f"The real filesystems visible in the mount namespace of process "
            f"`{name}` on {host} during run `{label}` were: "
            + ", ".join(f"`{m}`" for m in sorted(mount_points)[:12])
            + "."
        )

    return out
