"""Installation-wide process leases, separate from per-research worker locks."""

import os
import uuid
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock


def installation_root() -> Path | None:
    configured = os.environ.get("MARIANA_INSTALL_ROOT")
    root = Path(configured) if configured else Path(__file__).resolve().parents[2]
    if (root / ".git").exists() and (root / "pyproject.toml").is_file():
        return root.resolve()
    return None


def update_directory(root: Path) -> Path:
    directory = root / ".mariana-updates"
    directory.mkdir(exist_ok=True, mode=0o700)
    return directory


@contextmanager
def installation_lease(root: Path | None = None):
    root = root or installation_root()
    if root is None:
        yield
        return
    directory = update_directory(root)
    leases = directory / "processes"
    leases.mkdir(exist_ok=True, mode=0o700)
    path = leases / f"{os.getpid()}-{uuid.uuid4().hex}.lock"
    lock = FileLock(str(path))
    # Register under the same lock used by the updater, closing the start/update race.
    with FileLock(str(directory / "update.lock"), timeout=480):
        lock.acquire(timeout=0)
    try:
        yield
    finally:
        lock.release()
        path.unlink(missing_ok=True)
