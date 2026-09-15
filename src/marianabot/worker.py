"""Detached worker lifecycle. A file lock, not a remembered PID, owns execution."""

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from filelock import FileLock, Timeout

from marianabot.engine import Engine, Halt
from marianabot.reports import export_run
from marianabot.store import Store


def atomic_json(path: Path, data: dict):
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        # Windows readers/virus scanners may briefly deny delete-sharing on the target.
        # Keep the old complete file until replacement succeeds; never truncate it.
        for attempt in range(8):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


class WorkerManager:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.metadata = self.directory / "worker.json"
        self.children: list[subprocess.Popen] = []

    def read(self) -> dict:
        try:
            return json.loads(self.metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def active(self) -> dict | None:
        self.children[:] = [p for p in self.children if p.poll() is None]
        record = self.read()
        try:
            with FileLock(str(self.directory / "worker.lock"), timeout=0):
                if (
                    record.get("state") == "starting"
                    and time.time() - record.get("started", 0) < 20
                ):
                    return record
                return None
        except Timeout:
            if record.get("state") in ("starting", "running"):
                return record
            return {"state": "running", "run_id": None, "mode": "external"}

    async def start(self, run_id: str, *, messages_only: bool = False) -> bool:
        """Launch and await the lock handshake, without blocking the chat event loop."""
        launch_lock = FileLock(str(self.directory / "launch.lock"), timeout=0)
        try:
            launch_lock.acquire()
        except Timeout as exc:
            raise ValueError("A worker is starting. Try again in a moment.") from exc
        try:
            active = self.active()
            if active and active.get("run_id") == run_id and not messages_only:
                store = Store(self.directory)
                try:
                    cancelling = bool(store.run(run_id)["control"])
                finally:
                    store.close()
                if cancelling:
                    for _ in range(100):
                        await asyncio.sleep(0.1)
                        active = self.active()
                        if not active:
                            break
                    if active:
                        raise ValueError(
                            "The worker is still pausing. Try /resume again after it exits."
                        )
            if active:
                if active.get("run_id") == run_id:
                    if not messages_only and active.get("mode") == "messages":
                        raise ValueError("MB is answering. Wait for its reply, then /resume.")
                    return False
                raise ValueError(
                    "Another session is working. Use /sessions to open it and /pause before starting this one."
                )
            token = uuid.uuid4().hex
            record = {
                "run_id": run_id,
                "token": token,
                "state": "starting",
                "mode": "messages" if messages_only else "research",
                "started": time.time(),
            }
            atomic_json(self.metadata, record)
            command = [
                sys.executable,
                "-m",
                "marianabot.worker",
                run_id,
                "--data-dir",
                str(self.directory),
                "--token",
                token,
            ]
            if messages_only:
                command.append("--messages-only")
            options = (
                {"creationflags": subprocess.CREATE_NO_WINDOW}
                if os.name == "nt"
                else {"start_new_session": True}
            )
            try:
                with (self.directory / "worker.log").open("ab") as output:
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=output,
                        cwd=self.directory,
                        **options,
                    )
                self.children.append(process)
                for _ in range(150):
                    record = self.read()
                    if record.get("token") == token and record.get("state") in (
                        "running",
                        "finished",
                    ):
                        return True
                    if process.poll() is not None:
                        raise ValueError(
                            "Worker did not start. See " + str(self.directory / "worker.log")
                        )
                    await asyncio.sleep(0.1)
                # Do not start a second worker: the first may still acquire its lock.
                raise ValueError(
                    "Worker startup is taking longer than expected. Check /status and worker.log before retrying."
                )
            except OSError:
                atomic_json(self.metadata, record | {"state": "failed"})
                raise
            except ValueError:
                if process.poll() is not None:
                    atomic_json(self.metadata, record | {"state": "failed"})
                raise
        finally:
            launch_lock.release()


async def execute(store: Store, run_id: str, messages_only: bool):
    engine = Engine(store, run_id, messages_only=messages_only)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: engine.shutdown.set())
    if messages_only:
        # Supervise replies too: stop requests cancel active native client processes.
        task = asyncio.create_task(engine.respond())
        try:
            while not task.done():
                engine.check()
                await asyncio.sleep(0.25)
            await task
        except Halt as exc:
            store.event(run_id, "MB reply cancelled: " + str(exc))
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    else:
        await engine.run()


def main():
    parser = argparse.ArgumentParser(description="MarianaBot managed background worker")
    parser.add_argument("run_id")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--messages-only", action="store_true")
    args = parser.parse_args()
    store = Store(args.data_dir)
    metadata = store.directory / "worker.json"
    record = {
        "run_id": args.run_id,
        "token": args.token,
        "pid": os.getpid(),
        "mode": "messages" if args.messages_only else "research",
        "started": time.time(),
    }
    try:
        with FileLock(str(store.directory / "worker.lock"), timeout=0):
            run = store.run(args.run_id)
            if not args.messages_only and run["status"] in ("complete", "stopped"):
                raise ValueError("This research run is closed")
            try:
                if not args.messages_only:
                    store.update_run(args.run_id, control="")
                atomic_json(metadata, record | {"state": "running"})
                asyncio.run(execute(store, args.run_id, args.messages_only))
                export_run(store, args.run_id, store.directory / "exports" / args.run_id)
            finally:
                atomic_json(metadata, record | {"state": "finished"})
    finally:
        store.close()


if __name__ == "__main__":
    main()
