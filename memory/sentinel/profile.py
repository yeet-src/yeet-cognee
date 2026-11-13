import json
import pathlib
from dataclasses import asdict, dataclass, field

from .activity import Activity, listed
from .binaries import fingerprint

PROFILES = pathlib.Path(__file__).resolve().parent.parent / "profiles"


@dataclass(frozen=True)
class Finding:
    kind: str
    workload: str
    what: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.workload}: {self.what}"


@dataclass
class Profile:
    role: str
    programs: dict[str, str | None] = field(default_factory=dict)
    write_dirs: set[str] = field(default_factory=set)
    read_dirs: set[str] = field(default_factory=set)
    endpoints: set[str] = field(default_factory=set)
    peeks: bool = False
    windows: int = 0
    workloads: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, role: str) -> "Profile":
        path = PROFILES / f"{role}.json"
        if not path.exists():
            return cls(role)
        raw = json.loads(path.read_text())
        return cls(
            role=role, programs=raw["programs"], write_dirs=set(raw["write_dirs"]), read_dirs=set(raw["read_dirs"]),
            endpoints=set(raw["endpoints"]), peeks=raw["peeks"], windows=raw["windows"], workloads=set(raw["workloads"]),
        )

    def save(self) -> None:
        PROFILES.mkdir(exist_ok=True)
        raw = {k: sorted(v) if isinstance(v, set) else v for k, v in asdict(self).items()}
        (PROFILES / f"{self.role}.json").write_text(json.dumps(raw, indent=2) + "\n")

    @property
    def empty(self) -> bool:
        return self.windows == 0

    def learn(self, act: Activity) -> None:
        for program in act.programs:
            self.programs.setdefault(program, fingerprint(program).sha256)
        self.write_dirs |= act.write_dirs
        self.read_dirs |= act.read_dirs
        self.endpoints |= act.endpoints
        self.peeks |= act.peeks > 0
        self.workloads.add(act.name)
        self.windows += 1

    def findings(self, act: Activity) -> list[Finding]:
        policy = [
            *(f"attempted to open `{p}`" for p in act.sensitive),
            *(f"wrote to `{p}`, a program this host executes" for p in act.program_writes),
            *(f"ran `{p}`, whose binary ({fingerprint(p)}) is not the one the profile learned"
              for p in act.programs if self.programs.get(p) and self.programs[p] != fingerprint(p).sha256),
        ]
        novel = [
            *(f"ran `{p}`, which no `{self.role}` workload has run before" for p in act.programs if p not in self.programs),
            *(f"wrote under `{d}`, where no `{self.role}` workload has written before" for d in act.write_dirs - self.write_dirs),
            *(f"read under `{d}`, where no `{self.role}` workload has read before" for d in act.read_dirs - self.read_dirs),
            *(f"dialled `{e}`, which no `{self.role}` workload has dialled before" for e in act.endpoints - self.endpoints),
            *([f"read other processes' /proc entries {act.peeks} times; no `{self.role}` workload has done that before"]
              if act.peeks and not self.peeks else []),
        ]
        return [Finding("policy", act.name, w) for w in policy] + [Finding("novel", act.name, w) for w in novel]

    def allow(self, act: Activity) -> None:
        self.learn(act)

    def describe(self, host: str) -> str:
        programs = {p: fingerprint(p) for p in self.programs}
        return (
            f"# Profile of `{self.role}` workloads on host {host}\n\n"
            f"Learned from {self.windows} windows of {len(self.workloads)} workloads ({listed(self.workloads)}) "
            f"while they were known to be well behaved. A `{self.role}` workload on {host} normally runs "
            f"{listed(set(f'{p} ({b})' for p, b in programs.items())) or 'no other programs'}; writes under "
            f"{listed(self.write_dirs) or 'nowhere'}; reads under {listed(self.read_dirs) or 'nowhere outside system libraries'}; "
            f"dials {listed(self.endpoints) or 'nothing'}; and "
            f"{'does' if self.peeks else 'does not'} read other processes' /proc entries. "
            "Anything outside this profile is a finding until a human reviews it."
        )
