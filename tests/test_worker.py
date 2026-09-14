import asyncio
import time

import pytest

from marianabot.engine import Engine
from marianabot.worker import WorkerManager


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
