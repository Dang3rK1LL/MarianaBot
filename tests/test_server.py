import asyncio
import os
import time

import pytest

from marianabot.server import backup, recover
from marianabot.store import Store
from marianabot.worker import WorkerManager, atomic_json


async def until(predicate):
    async with asyncio.timeout(15):
        while not predicate():
            await asyncio.sleep(0.05)


@pytest.mark.parametrize(
    ("status", "reason", "control", "mode", "expected"),
    [
        ("running", "", "", "research", True),
        ("paused", "Worker interrupted; resume to continue", "", "research", True),
        ("paused", "Owner requested pause", "pause", "research", False),
        ("running", "", "stop", "research", False),
        ("paused", "Login expired", "", "research", False),
        ("complete", "Configured round limit reached", "", "research", False),
        ("stopped", "Owner requested stop", "stop", "research", False),
        ("running", "", "", "messages", False),
    ],
)
async def test_boot_recovery_respects_owner_and_failure_pauses(
    store, config, status, reason, control, mode, expected
):
    run_id = store.create_run("Server recovery", config, demo=True)
    store.update_run(run_id, status=status, reason=reason, control=control)
    atomic_json(
        store.directory / "worker.json",
        {"run_id": run_id, "mode": mode, "state": "running"},
    )
    assert await recover(store.directory) is expected
    if expected:
        manager = WorkerManager(store.directory)
        await until(lambda: manager.active() is None)
        assert store.run(run_id)["status"] == "complete"
    else:
        assert store.run(run_id)["status"] == status
        assert not store.calls(run_id)


async def test_recovery_does_not_duplicate_active_worker(store, config):
    config.rb.agents = config.jb.agents = 12
    config.rb.concurrency = config.jb.concurrency = 1
    run_id = store.create_run("Only one worker", config, demo=True)
    manager = WorkerManager(store.directory)
    await manager.start(run_id)
    try:
        assert not await recover(store.directory)
    finally:
        store.update_run(run_id, control="pause")
        await until(lambda: manager.active() is None)


def test_backup_includes_wal_and_retains_week(store, config, tmp_path):
    run_id = store.create_run("WAL-backed research", config, demo=True)
    store.update_run(run_id, brief="Keep this exact evidence")
    destination = tmp_path / "backups"
    first = backup(store.directory, destination)
    old = time.time() - 8 * 86400
    os.utime(first, (old, old))
    second = backup(store.directory, destination)
    assert not first.exists()
    restored_dir = tmp_path / "restore"
    restored_dir.mkdir()
    (restored_dir / "mariana.sqlite3").write_bytes(second.read_bytes())
    restored = Store(restored_dir)
    try:
        assert restored.run(run_id)["brief"] == "Keep this exact evidence"
    finally:
        restored.close()
