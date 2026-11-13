"""A cached baseline of the sentinel's learned memory.

Seeding the sandbox policy into cognee and learning a clean fleet's profile are
the two slow, non-deterministic steps of a cold start: each runs a local model
over a fresh run every time. This module captures their result -- cognee's
graph and vector store, plus the learned profiles -- as one archive, so a demo
restores an identical baseline in a second instead of recomputing it.

The readable source of the baseline stays in context/*.md (the policy and host
history) and scenario/ (what a clean agent does); the archive only caches the
derived store, and is rebuilt with `save` whenever that source, or the model
behind it, changes.
"""

import os
import pathlib
import shutil
import tarfile

MEMORY = pathlib.Path(__file__).resolve().parent.parent
STORE = MEMORY / ".cognee-store"
PROFILES = MEMORY / "profiles"
ARCHIVE = MEMORY / "baseline" / "baseline.tar.gz"

CONTENTS = (STORE, PROFILES)


def configure() -> None:
    """Pin cognee's store to a stable, project-local path before it is imported.

    cognee demands an absolute path, which we would not want to bake into a
    committed .env, so we derive one from this package's own location. This is
    authoritative: it is also the path `save` and `restore` archive.
    """
    os.environ["DATA_ROOT_DIRECTORY"] = str(STORE / "data")
    os.environ["SYSTEM_ROOT_DIRECTORY"] = str(STORE / "system")


def exists() -> bool:
    return ARCHIVE.is_file()


def save() -> pathlib.Path:
    present = [path for path in CONTENTS if path.exists()]
    if not present:
        raise FileNotFoundError(f"Nothing to snapshot yet: neither {STORE} nor {PROFILES} exists")
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(ARCHIVE, "w:gz") as tar:
        for path in present:
            tar.add(path, arcname=path.relative_to(MEMORY).as_posix())
    return ARCHIVE


def restore() -> bool:
    if not exists():
        return False
    for path in CONTENTS:
        shutil.rmtree(path, ignore_errors=True)
    with tarfile.open(ARCHIVE, "r:gz") as tar:
        tar.extractall(MEMORY, filter="data")
    return True


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Save or restore the sentinel's baseline memory (cognee store + learned profiles).")
    ap.add_argument("action", choices=("save", "restore", "where"))
    args = ap.parse_args(argv)
    configure()

    if args.action == "save":
        print(f"saved baseline -> {save()}")
    elif args.action == "restore":
        print("restored baseline" if restore() else f"no baseline to restore at {ARCHIVE}")
    else:
        print(f"store    {STORE}")
        print(f"profiles {PROFILES}")
        print(f"archive  {ARCHIVE} ({'present' if exists() else 'absent'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
