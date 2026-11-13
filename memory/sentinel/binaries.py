import functools
import hashlib
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Binary:
    sha256: str | None
    package: str | None

    def __str__(self) -> str:
        digest = self.sha256[:12] if self.sha256 else "unreadable"
        return f"{digest} from {self.package}" if self.package else digest


@functools.lru_cache(maxsize=4096)
def fingerprint(path: str) -> Binary:
    return Binary(sha256=_digest(path), package=_package(path))


def _digest(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            return hashlib.file_digest(fh, "sha256").hexdigest()
    except OSError:
        return None


def _package(path: str) -> str | None:
    for tool, args in (("rpm", ["-qf", "--queryformat", "%{NAME}"]), ("dpkg-query", ["-S"])):
        if not shutil.which(tool):
            continue
        done = subprocess.run([tool, *args, path], capture_output=True, text=True)
        if done.returncode == 0:
            return done.stdout.split(":")[0].strip()
    return None
