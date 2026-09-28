"""Boot recovery and consistent database backups for a single-user Linux server."""

import argparse
import asyncio
import os
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock, Timeout

from marianabot.store import Store
from marianabot.worker import WorkerManager, atomic_json


def resumable(record: dict, run: dict) -> bool:
    if record.get("mode") != "research" or run["control"]:
        return False
    if run["status"] == "running":
        return True
    if run["status"] == "ready":
        return record.get("state") == "starting"
    return run["status"] == "paused" and run["reason"] == "Worker interrupted; resume to continue"


async def recover(directory: Path) -> bool:
    """Resume interrupted research, never an owner pause or a provider/setup error."""
    manager = WorkerManager(directory)
    try:
        with (
            FileLock(str(directory / "launch.lock"), timeout=0),
            FileLock(str(directory / "worker.lock"), timeout=0),
        ):
            record = manager.read()
            if not record.get("run_id"):
                return False
            store = Store(directory)
            try:
                run = store.run(record["run_id"])
                if not resumable(record, run):
                    return False
            finally:
                store.close()
            # The real lock is free: a stale "starting" record is not a live worker.
            atomic_json(manager.metadata, record | {"state": "finished"})
    except Timeout:
        return False
    return await manager.start(record["run_id"])


def backup(directory: Path, destination: Path) -> Path:
    """SQLite's online backup includes committed WAL data without stopping research."""
    source = directory.resolve() / "mariana.sqlite3"
    if not source.is_file():
        raise ValueError("No research database to back up")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = destination / f"mariana-{stamp}.sqlite3"
    temporary = target.with_suffix(".tmp")
    try:
        with (
            closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original,
            closing(sqlite3.connect(temporary)) as snapshot,
        ):
            original.backup(snapshot)
            if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup integrity check failed")
        temporary.chmod(0o600)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    for old in destination.glob("mariana-*.sqlite3"):
        if old != target and old.stat().st_mtime < time.time() - 7 * 86400:
            old.unlink()
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("recover", "backup"))
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    if args.action == "recover":
        resumed = asyncio.run(recover(args.data_dir))
        print("Interrupted research resumed." if resumed else "No research needs recovery.")
    elif args.destination is None:
        parser.error("backup needs --destination")
    else:
        print(backup(args.data_dir, args.destination))


if __name__ == "__main__":
    main()
