import asyncio
import os
import time
from threading import Event
from types import SimpleNamespace

import pytest

from marianabot.server import backup, discord_service, recover
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


def test_discord_supervisor_stops_only_its_owned_tmux_session(tmp_path, monkeypatch):
    import shlex

    monkeypatch.setattr("marianabot.server.sys.platform", "linux")
    monkeypatch.setattr("marianabot.server.installation_root", lambda: tmp_path)
    calls = []

    def tmux(args, **kwargs):
        calls.append(args)
        if "show-options" in args:
            return SimpleNamespace(returncode=0, stdout="off\n")
        if "has-session" in args:
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(returncode=0, stdout="$17\n")

    monkeypatch.setattr("marianabot.server.subprocess.run", tmux)
    stop = Event()
    stop.set()
    discord_service(tmp_path / "research state", tmp_path / "private settings.toml", stop=stop)
    launched = next(args for args in calls if "new-session" in args)
    command = shlex.split(launched[-1])
    assert "--notifications-only" not in command
    assert command[command.index("--config") + 1] == str(tmp_path / "private settings.toml")
    assert command[command.index("--data-dir") + 1] == str(tmp_path / "research state")
    assert calls[-1] == ["tmux", "-L", "marianabot", "kill-session", "-t", "$17"]
    assert all("kill-server" not in args for args in calls)


@pytest.mark.parametrize("existing", [False, True])
def test_discord_supervisor_refuses_missing_server_and_duplicate_bot(
    tmp_path, monkeypatch, existing
):
    monkeypatch.setattr("marianabot.server.sys.platform", "linux")
    calls = []

    def tmux(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0 if existing else 1, stdout="off\n")

    monkeypatch.setattr("marianabot.server.subprocess.run", tmux)
    with pytest.raises(ValueError, match="already exists" if existing else "terminal.service"):
        discord_service(tmp_path, tmp_path / "discord.toml")
    assert all("new-session" not in args and "kill-session" not in args for args in calls)


def test_discord_supervisor_exits_when_bot_disappears(tmp_path, monkeypatch):
    monkeypatch.setattr("marianabot.server.sys.platform", "linux")
    monkeypatch.setattr("marianabot.server.installation_root", lambda: tmp_path)
    calls = []

    def tmux(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            returncode=1 if "has-session" in args else 0,
            stdout="off\n" if "show-options" in args else "$17\n",
        )

    monkeypatch.setattr("marianabot.server.subprocess.run", tmux)
    stop = SimpleNamespace(wait=lambda seconds: False, set=lambda: None)
    with pytest.raises(ValueError, match="bot exited"):
        discord_service(tmp_path, tmp_path / "discord.toml", stop=stop)
    assert calls[-1][-3:] == ["kill-session", "-t", "$17"]
