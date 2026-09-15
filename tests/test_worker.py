import asyncio
import sys
import time
from pathlib import Path

import pytest

from marianabot.engine import Engine
from marianabot.worker import WorkerManager, atomic_json


async def test_worker_survives_launcher_process_exit(store, config):
    config.rb.agents = config.jb.agents = 12
    config.rb.concurrency = config.jb.concurrency = 1
    run_id = store.create_run("Outlive the launcher process", config, demo=True)
    code = (
        "import asyncio,sys; from pathlib import Path; "
        "from marianabot.worker import WorkerManager; "
        "asyncio.run(WorkerManager(Path(sys.argv[1])).start(sys.argv[2]))"
    )
    parent = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        str(store.directory),
        run_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, error = await asyncio.wait_for(parent.communicate(), 15)
    assert parent.returncode == 0, error.decode(errors="replace")
    manager = WorkerManager(store.directory)
    try:
        assert manager.active()  # The launcher has exited; research still owns its lock.
        await until(lambda: manager.active() is None)
        assert store.run(run_id)["status"] == "complete"
    finally:
        if manager.active():
            store.update_run(run_id, control="pause")
            await until(lambda: manager.active() is None)


async def until(predicate, timeout=15):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.05)


async def test_detached_research_reconnect_and_post_completion_chat(store, config):
    run_id = store.create_run("Independent process demo", config, demo=True)
    first = WorkerManager(store.directory)
    assert await first.start(run_id)
    # Another UI instance can attach; the worker isn't tied to the original manager.
    second = WorkerManager(store.directory)
    await until(lambda: store.run(run_id)["status"] == "complete")
    await until(lambda: second.active() is None)
    assert len(store.rounds(run_id)) == 2
    assert {m["role"] for m in store.messages(run_id)} == {"you", "MB", "RB", "JB"}
    assert (store.directory / "exports" / run_id / "report.md").is_file()
    store.enqueue(run_id, "ask", "Explain the remaining assumptions")
    assert await second.start(run_id, messages_only=True)
    await until(lambda: second.active() is None)
    assert store.commands(run_id)[0]["answer"]
    assert store.run(run_id)["status"] == "complete"
    assert len(store.rounds(run_id)) == 2
    assert all(p.poll() is not None for p in first.children)


async def test_worker_lock_and_pause(store, config):
    config.research.max_rounds = 24
    config.research.plateau_rounds = 24
    config.research.approval_streak = 24
    run_id = store.create_run("Pause background demo", config, demo=True)
    other = store.create_run("Second run", config, demo=True)
    manager = WorkerManager(store.directory)
    await manager.start(run_id)
    try:
        assert not await WorkerManager(store.directory).start(run_id)
        with pytest.raises(ValueError, match="Another session"):
            await WorkerManager(store.directory).start(other)
    finally:
        store.update_run(run_id, control="pause")
        await until(lambda: manager.active() is None)
    assert store.run(run_id)["status"] == "paused"
    assert not any(c["state"] == "running" for c in store.calls(run_id))


async def test_paused_chat_does_not_resume_or_expire_research(store, config):
    run_id = store.create_run("Paused run", config, demo=True)
    store.update_run(run_id, status="paused", control="pause")
    with store.db:
        store.db.execute("UPDATE runs SET created=? WHERE id=?", (time.time() - 1e7, run_id))
    store.enqueue(run_id, "ask", "Where were we?")
    await Engine(store, run_id, messages_only=True).respond()
    assert store.commands(run_id)[0]["answer"]
    assert store.run(run_id)["status"] == "paused"
    assert not store.rounds(run_id)
    assert [c["brain"] for c in store.calls(run_id)] == ["MB"]


async def test_long_problem_reaches_intake_and_transcript_survives_resume(store, config):
    problem = "Business context. " * 5000 + "UNIQUE END CONSTRAINT"
    run_id = store.create_run(problem, config, demo=True)
    await Engine(store, run_id).run()
    intake = store.cached(run_id, "mb-intake")
    assert "UNIQUE END CONSTRAINT" in intake["prompt"]
    before = store.messages(run_id)
    await Engine(store, run_id).run()
    assert store.messages(run_id) == before
    assert before[0]["text"] == problem


def test_metadata_replacement_retries_windows_sharing_violation(tmp_path, monkeypatch):
    replace = Path.replace
    attempts = []

    def temporarily_locked(source, target):
        attempts.append(target)
        if len(attempts) < 3:
            raise PermissionError("Target temporarily open without delete sharing")
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", temporarily_locked)
    atomic_json(tmp_path / "worker.json", {"state": "running"})
    assert len(attempts) == 3
    assert (tmp_path / "worker.json").read_text() == '{"state": "running"}'
    assert not list(tmp_path.glob("*.tmp"))


async def test_repeated_pause_cancels_mb_without_reopening_research(store, config):
    from marianabot.engine import Halt

    run_id = store.create_run("Finished research", config, demo=True)
    store.update_run(run_id, status="complete", control="pause")
    engine = Engine(store, run_id, messages_only=True)
    engine.check()  # Old pause belongs to the research, not this new conversation request.
    store.update_run(run_id, control="pause")
    with pytest.raises(Halt):
        engine.check()
    assert store.run(run_id)["status"] == "complete"


async def test_full_long_owner_message_is_in_mb_context(store, config):
    run_id = store.create_run("Business problem", config, demo=True)
    store.update_run(run_id, status="paused")
    message = "Owner context. " * 1200 + "UNIQUE END OF OWNER MESSAGE"
    command_id = store.enqueue(run_id, "ask", message)
    await Engine(store, run_id, messages_only=True).respond()
    assert (
        "UNIQUE END OF OWNER MESSAGE" in store.cached(run_id, f"mb-command-{command_id}")["prompt"]
    )
