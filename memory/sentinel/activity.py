import posixpath
import re
import socket
from collections import Counter
from dataclasses import dataclass, field

from .policy import KNOWN_HOSTS, in_program_dir, is_sensitive

PROC_PID = re.compile(r"^/proc(/\d+)?(?=/|$)")


@dataclass
class Activity:
    name: str
    pid: int
    cmdline: str
    cwd: str | None
    events: int
    children: int
    truncated: int
    programs: Counter = field(default_factory=Counter)
    reads: Counter = field(default_factory=Counter)
    writes: Counter = field(default_factory=Counter)
    sensitive: Counter = field(default_factory=Counter)
    program_writes: Counter = field(default_factory=Counter)
    peeks: int = 0
    connects: Counter = field(default_factory=Counter)
    blocked: set = field(default_factory=set)

    @property
    def role(self) -> str:
        return role_of(self.name)

    @property
    def read_dirs(self) -> set[str]:
        return {directory(p, self.name) for p in self.reads}

    @property
    def write_dirs(self) -> set[str]:
        return {directory(p, self.name) for p in self.writes}

    @property
    def endpoints(self) -> set[str]:
        return {endpoint(addr, port) for addr, port in self.connects}

    @property
    def opened(self) -> set[str]:
        return set(self.reads) | set(self.writes) | set(self.sensitive) | set(self.program_writes)


def workload_name(cmdline: list[str], comm: str | None) -> str:
    for i, arg in enumerate(cmdline):
        if arg == "--name" and i + 1 < len(cmdline):
            return cmdline[i + 1]
        if arg.startswith("--name="):
            return arg.split("=", 1)[1]
    return posixpath.basename(cmdline[0]) if cmdline else comm or "unknown"


def role_of(name: str) -> str:
    return re.sub(r"[-_.]?\d+$", "", name) or name


def directory(path: str, name: str) -> str:
    return posixpath.dirname(path).replace(f"/{name}", "/<name>")


def summarize(group: dict) -> Activity:
    cmdline = group.get("cmdline") or []
    act = Activity(
        name=workload_name(cmdline, group.get("comm")), pid=group["pid"], cmdline=" ".join(cmdline),
        cwd=group.get("cwd"), events=len(group.get("events") or []), children=len(group.get("children") or []),
        truncated=int(group.get("truncated") or 0),
    )
    for event in group.get("events") or []:
        _fold(act, event)
    return act


def _fold(act: Activity, event: dict) -> None:
    kind = event["kind"]
    if kind == "exec":
        act.programs[event["filename"]] += 1
    elif kind == "connect":
        act.connects[(event["addr"], int(event["port"]))] += 1
    elif kind == "open":
        _fold_open(act, event)


def _fold_open(act: Activity, event: dict) -> None:
    path = event["path"]
    if event.get("blocked"):
        act.blocked.add(path)
    writing = event["access"] != "read" or event.get("create")
    if is_sensitive(path):
        act.sensitive[path] += 1
    elif writing and (event.get("executed") or in_program_dir(path)):
        act.program_writes[path] += 1
    elif PROC_PID.match(path):
        act.peeks += 1
    elif writing:
        act.writes[path] += 1
    else:
        act.reads[path] += 1


_names: dict[str, str | None] = {}


def _resolve_known() -> None:
    for host in KNOWN_HOSTS:
        try:
            for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
                _names[info[4][0]] = host
        except OSError:
            pass


def hostname(addr: str) -> str | None:
    if addr not in _names:
        _resolve_known()
    if addr not in _names:
        try:
            _names[addr] = socket.gethostbyaddr(addr)[0]
        except OSError:
            _names[addr] = None
    return _names[addr]


def endpoint(addr: str, port: int) -> str:
    return f"{hostname(addr) or addr}:{port}"


def listed(items: Counter | set, limit: int = 8) -> str:
    counts = items.most_common(limit) if isinstance(items, Counter) else [(k, 1) for k in sorted(items)[:limit]]
    text = ", ".join(f"`{k}`" + (f" ({n} times)" if n > 1 else "") for k, n in counts)
    rest = len(items) - len(counts)
    return text + (f" and {rest} more" if rest > 0 else "")


def brief(act: Activity) -> str:
    parts = [
        f"ran {listed(act.programs, 4)}" if act.programs else "",
        f"wrote {listed(act.writes, 3)}" if act.writes else "",
        f"read {listed(act.reads, 3)}" if act.reads else "",
        f"attempted {listed(act.sensitive, 3)}" if act.sensitive else "",
        f"wrote to programs {listed(act.program_writes, 3)}" if act.program_writes else "",
        f"peeked at /proc {act.peeks} times" if act.peeks else "",
        f"dialled {listed(act.endpoints, 3)}" if act.connects else "",
        f"was BLOCKED opening {listed(act.blocked, 3)}" if act.blocked else "",
    ]
    return "; ".join(p for p in parts if p)


def describe(act: Activity, host: str) -> str:
    sentences = [
        f"Workload `{act.name}` (pid {act.pid}, `{act.cmdline}`, working in `{act.cwd}`) on host {host}: "
        f"the kernel recorded {act.events} actions by it and its {act.children} child processes"
        + (f", {act.truncated} more were not kept" if act.truncated else "") + ".",
        f"It ran {listed(act.programs)}." if act.programs else "It ran no other programs.",
        f"It wrote under {listed(act.write_dirs)}." if act.writes else "It wrote no files.",
        f"It read files under {listed(act.read_dirs)}." if act.reads else "It read no files outside system libraries.",
        f"It dialled {listed(act.endpoints)}." if act.connects else "It made no outbound connections.",
    ]
    if act.sensitive:
        sentences.append(
            f"It ATTEMPTED TO OPEN the privileged or host-revealing paths {listed(act.sensitive)}; "
            "an attempt is recorded whether or not the kernel allowed it."
        )
    if act.program_writes:
        sentences.append(f"It WROTE TO PROGRAMS this host executes: {listed(act.program_writes)}.")
    if act.peeks:
        sentences.append(f"It read other processes' /proc entries {act.peeks} times.")
    return " ".join(sentences)
