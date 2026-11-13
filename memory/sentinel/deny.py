"""Compile the policy's sensitive paths and the learned rules into the sets the
LSM guards enforce:

- read_inodes: concrete files that may not be opened, resolved to (device,
  inode) now. The inode holds even when the file is reached by another name.
- basenames: final-component patterns (`.env` prefix, `.pem` suffix), matched
  against the opened file's own name wherever it lives.
- dirs: directory names matched against every resolved ancestor, so a rule for
  a directory anywhere (`*/secrets/*`) enforces instead of staying advisory.
- write_inodes / exec_inodes: files a watched agent may not overwrite or run,
  from tamper/exec rulings and the protected-executable policy.
- protect_exec / protect_ptrace: block writes to any executable, and block
  reading another process's memory.

A pattern that can't be reduced to one of these (a glob with no directory
component, an exec/write rule for a file that doesn't exist yet) is reported as
unresolved and stays a finding."""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field

from . import policy, rules

HOME = pathlib.Path.home()

# Known secret filenames safe to deny by basename. A name like `mem` or `maps`
# is NOT here: blocking it would also stop a process reading its own
# /proc/self/maps, which is normal; those stay findings, not blocks.
KEY_FILES = ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa")


@dataclass
class DenySets:
    read_inodes: list[dict] = field(default_factory=list)
    write_inodes: list[dict] = field(default_factory=list)
    exec_inodes: list[dict] = field(default_factory=list)
    basenames: list[dict] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)
    protect_exec: bool = False
    protect_ptrace: bool = False
    unresolved: list[str] = field(default_factory=list)


def _enforceable_basename(name: str) -> bool:
    return name.startswith(".") or name in KEY_FILES


def _inode(path: str) -> dict | None:
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return None
    # The kernel's super_block.s_dev is dev_t in the new encoding
    # (major << 20 | minor); userspace st_dev uses glibc's own encoding, so
    # re-encode from the decoded major/minor to match what BPF reads.
    dev = (os.major(st.st_dev) << 20) | os.minor(st.st_dev)
    return {"dev": dev, "ino": st.st_ino, "path": path}


def _concrete_candidates(text: str) -> list[str]:
    if text.startswith("/"):
        return [text]
    name = text.lstrip("*")
    if "/" in name:
        return [str(HOME / name.lstrip("/"))]
    return []


def _dir_name(pattern: str) -> str | None:
    inner = pattern[:-2] if pattern.endswith("/*") else pattern
    name = inner.rstrip("/").rsplit("/", 1)[-1]
    return name if name and not any(c in name for c in "*?[") else None


def sets() -> DenySets:
    out = DenySets(
        protect_exec=policy.PROTECT_EXEC_WRITES,
        protect_ptrace=policy.PROTECT_PTRACE,
    )
    read: dict[tuple[int, int], dict] = {}
    writes: dict[tuple[int, int], dict] = {}
    execs: dict[tuple[int, int], dict] = {}

    def into(bucket):
        def add(path: str) -> bool:
            info = _inode(path)
            if info:
                bucket[(info["dev"], info["ino"])] = info
                return True
            return False
        return add

    for prefix in policy.SENSITIVE_PREFIXES:
        into(read)(prefix.rstrip("/"))
    for suffix in policy.SENSITIVE_SUFFIXES:
        name = suffix.lstrip("/")
        if "/" in name:
            if not any(into(read)(c) for c in _concrete_candidates(suffix)):
                out.unresolved.append(suffix)
        elif _enforceable_basename(name):
            out.basenames.append({"kind": "suffix", "text": name})
        else:
            out.unresolved.append(suffix)
    for path in policy.PROTECTED_EXECUTABLES:
        if not into(writes)(path):
            out.unresolved.append(path)

    for rule in rules.load():
        add = into({"write": writes, "exec": execs}.get(rule.action, read))
        for pattern in rule.patterns:
            if rule.action == "read":
                _fold_read(pattern, add, out)
            elif not any(add(c) for c in _concrete_candidates(pattern) or [pattern]):
                out.unresolved.append(pattern)

    out.read_inodes = list(read.values())
    out.write_inodes = list(writes.values())
    out.exec_inodes = list(execs.values())
    out.basenames = _dedup(out.basenames)
    out.dirs = sorted(set(out.dirs))
    out.unresolved = sorted(set(out.unresolved))
    return out


def _fold_read(pattern: str, add, out: DenySets) -> None:
    p = pattern.strip()
    if not p:
        return
    if p.endswith("/*"):
        name = _dir_name(p)
        out.dirs.append(name) if name else out.unresolved.append(pattern)
        return
    if p.startswith("*/") and p.endswith("*") and "/" not in p[2:-1]:
        out.basenames.append({"kind": "prefix", "text": p[2:-1]})
        return
    if p.startswith("*") and not p.endswith("*"):
        text = p[1:]
        if "/" in text:
            if not any(add(c) for c in _concrete_candidates(p)):
                out.unresolved.append(pattern)
        elif _enforceable_basename(text):
            out.basenames.append({"kind": "suffix", "text": text})
        else:
            out.unresolved.append(pattern)
        return
    if not any(c in p for c in "*?["):
        if not add(p):
            out.unresolved.append(pattern)
        return
    out.unresolved.append(pattern)


def _dedup(entries: list[dict]) -> list[dict]:
    seen, result = set(), []
    for e in entries:
        key = (e["kind"], e["text"])
        if e["text"] and key not in seen:
            seen.add(key)
            result.append(e)
    return result
