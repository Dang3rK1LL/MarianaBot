"""Linux service helpers for research recovery, backups and Discord supervision."""

import argparse
import asyncio
import os
import shlex
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from filelock import FileLock, Timeout

from marianabot.runtime import installation_root
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


def discord_service(directory: Path, config: Path, *, stop: Event | None = None):
    """Supervise the bot in the terminal service's tmux server so workers survive bot restarts."""
    if sys.platform != "linux":
        raise ValueError("The managed Discord service requires the Linux terminal service.")

    def tmux(*args):
        return subprocess.run(["tmux", "-L", "marianabot", *args], capture_output=True, text=True)

    server = tmux("show-options", "-s", "-v", "exit-empty")
    if server.returncode or server.stdout.strip() != "off":
        raise ValueError("Start marianabot-terminal.service before the managed Discord service.")
    if not tmux("has-session", "-t", "=mariana-discord").returncode:
        raise ValueError(
            "A mariana-discord tmux session already exists; do not start a second bot."
        )
    root = installation_root() or Path.cwd()
    command = shlex.join(
        [
            "env",
            f"MARIANA_INSTALL_ROOT={root}",
            sys.executable,
            "-m",
            "marianabot",
            "--no-update",
            "discord",
            "run",
            "--config",
            str(config.resolve()),
            "--data-dir",
            str(directory.resolve()),
        ]
    )
    started = tmux(
        "new-session",
        "-d",
        "-P",
        "-F",
        "#{session_id}",
        "-s",
        "mariana-discord",
        "-c",
        str(root),
        command,
    )
    if started.returncode or not started.stdout.strip():
        raise ValueError("Could not start the managed Discord session. Check the terminal service.")
    session = started.stdout.strip()
    stop = stop or Event()
    handlers = {}
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            handlers[sig] = signal.signal(sig, lambda *_: stop.set())
        while not stop.wait(1):
            if tmux("has-session", "-t", session).returncode:
                raise ValueError("The Discord bot exited. Check its private settings and token.")
    finally:
        tmux("kill-session", "-t", session)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("recover", "backup", "discord"))
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--config", type=Path, default=Path("discord.toml"))
    args = parser.parse_args()
    if args.action == "recover":
        resumed = asyncio.run(recover(args.data_dir))
        print("Interrupted research resumed." if resumed else "No research needs recovery.")
    elif args.action == "discord":
        try:
            discord_service(args.data_dir, args.config)
        except (ValueError, OSError) as exc:
            parser.exit(1, str(exc) + "\n")
    elif args.destination is None:
        parser.error("backup needs --destination")
    else:
        print(backup(args.data_dir, args.destination))


if __name__ == "__main__":
    main()
