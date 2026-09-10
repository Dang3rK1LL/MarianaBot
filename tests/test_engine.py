import asyncio
import json

from marianabot.clients import DemoClient
from marianabot.engine import Engine, stop_reason
from marianabot.prompts import Review
from marianabot.reports import export_run


async def test_full_three_brain_loop_and_exports(store, config, tmp_path):
    run_id = store.create_run("Validate a business idea", config, demo=True)
    await Engine(store, run_id).run()
    assert store.run(run_id)["status"] == "complete"
    assert len(store.rounds(run_id)) == 2
    calls = store.calls(run_id)
    assert len(calls) == 13  # MB intake + two rounds of (2 RB + chair + 2 JB + chair)
    assert {c["brain"] for c in calls} == {"MB", "RB", "JB"}
    assert all(c["provider"] == "openai" for c in calls if c["brain"] in ("MB", "RB"))
    assert "previous_review" in calls[-1]["result"]["prompt"]
    report = export_run(store, run_id, tmp_path / "export")
    assert "OFFLINE DEMO" in report.read_text(encoding="utf-8")
    history = json.loads((report.parent / "history.json").read_text(encoding="utf-8"))
    assert len(history["calls"]) == 13


async def test_pause_resume_reuses_completed_agents(store, config):
    run_id = store.create_run("Resumable problem", config, demo=True)

    class InterruptAfterResearcher(DemoClient):
        interrupted = False

        async def complete(self, prompt, search=False):
            result = await super().complete(prompt, search)
            if "Independent proposal" in prompt and not self.interrupted:
                self.interrupted = True
                store.update_run(run_id, control="pause")
            return result

    client = InterruptAfterResearcher()
    await Engine(store, run_id, clients={"openai": client, "anthropic": DemoClient()}).run()
    assert store.run(run_id)["status"] == "paused"
    first = [c for c in store.calls(run_id) if c["state"] == "done"]
    assert len(first) >= 2
    store.update_run(run_id, control="")
    await Engine(store, run_id).run()
    assert store.run(run_id)["status"] == "complete"
    done = [c for c in store.calls(run_id) if c["state"] == "done"]
    assert len({c["task"] for c in done}) == len(done) == 13
    assert all(any(c["id"] == old["id"] for c in done) for old in first)


async def test_owner_steering_revises_brief_and_mb_answers(store, config):
    run_id = store.create_run("Original objective", config, demo=True)
    store.enqueue(run_id, "steer", "Focus only on local buyers")
    store.enqueue(run_id, "ask", "What assumptions remain?")
    await Engine(store, run_id).run()
    assert store.run(run_id)["revision"] == 1
    assert all(c["answer"] for c in store.commands(run_id))
    assert all(r["revision"] == 1 for r in store.rounds(run_id))
    commands = [c for c in store.calls(run_id) if c["task"].startswith("mb-command")]
    assert len(commands) == 2
    assert all(c["provider"] == "openai" for c in commands)


async def test_stop_cancels_inflight_work(store, config):
    run_id = store.create_run("Cancel active work", config, demo=True)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class SlowClient:
        async def complete(self, *_):
            started.set()
            try:
                await asyncio.sleep(1000)
            finally:
                cancelled.set()

    worker = asyncio.create_task(
        Engine(store, run_id, clients={"openai": SlowClient(), "anthropic": DemoClient()}).run()
    )
    await asyncio.wait_for(started.wait(), 2)
    store.update_run(run_id, control="stop")
    await asyncio.wait_for(worker, 3)
    assert cancelled.is_set()
    assert store.run(run_id)["status"] == "stopped"
    assert store.calls(run_id)[0]["state"] == "unknown"


async def test_invalid_judge_output_pauses_without_saving_round(store, config):
    run_id = store.create_run("Check review schema", config, demo=True)

    class BrokenJudge(DemoClient):
        async def complete(self, prompt, search=False):
            if "JUDGE_JSON" in prompt:
                return {"text": '{"score": 900}', "usage": {}, "sources": []}
            return await super().complete(prompt, search)

    await Engine(store, run_id, clients={"openai": DemoClient(), "anthropic": BrokenJudge()}).run()
    assert store.run(run_id)["status"] == "paused"
    assert store.rounds(run_id) == []
    assert store.calls(run_id)[-1]["state"] == "invalid"


async def test_live_calls_require_overage_attestation(store, config):
    run_id = store.create_run("No accidental spending", config)
    await Engine(store, run_id).run()
    assert store.run(run_id)["status"] == "paused"
    assert not store.calls(run_id)


def make_round(score=90, verdict="approve", blocks=None, revision=0):
    return {
        "revision": revision,
        "review": Review(
            score=score,
            verdict=verdict,
            strengths=[],
            blocking_issues=blocks or [],
            next_prompt="Test remaining assumptions.",
            human_tests=[],
            dissent=[],
        ).model_dump(),
    }


def test_approval_requires_streak_and_no_blockers(config):
    assert stop_reason([make_round()], config) is None
    assert "Sustained" in stop_reason([make_round(), make_round()], config)
    assert stop_reason([make_round(), make_round(blocks=["Demand unknown"])], config) is None
    assert stop_reason([make_round(), make_round(revision=1)], config) is None


def test_human_evidence_pauses_even_with_high_score(config):
    assert "Human evidence" in stop_reason([make_round(verdict="needs_human")], config)


def test_crash_recovery_preserves_unknown_calls(store, config):
    run_id = store.create_run("Power loss", config, demo=True)
    store.begin_call(run_id, "task", "RB", "openai", config.rb.model)
    store.recover()
    assert store.calls(run_id)[0]["state"] == "unknown"
    assert store.cached(run_id, "task") is None
