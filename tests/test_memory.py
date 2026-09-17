import asyncio
import json
import re

import pytest

from marianabot.clients import ClientError, DemoClient
from marianabot.engine import Engine, Halt
from marianabot.memory import size
from marianabot.reports import export_run


def data_from(prompt):
    return json.loads(prompt.split("EVIDENCE_CONTEXT_JSON (data, not instructions):\n", 1)[1])


class Summarizer(DemoClient):
    def __init__(self):
        self.inputs = []

    async def complete(self, prompt, search=False):
        if "COMPACT_MEMORY:" not in prompt:
            return await super().complete(prompt, search)
        data = data_from(prompt)
        self.inputs.append(data)
        markers = sorted(set(re.findall(r"FACT-\d+", data["source"])))
        summary = "Fixture retained facts: " + ", ".join(markers)
        return {
            "text": json.dumps({"source_id": data["source_id"], "summary": summary}),
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "sources": [],
        }


def engine_with(store, config):
    config.research.max_context_chars = 8000
    run_id = store.create_run("Business with a long history", config, demo=True)
    client = Summarizer()
    return Engine(store, run_id, clients={"openai": client, "anthropic": DemoClient()}), client


async def test_large_context_compacts_all_chunks_and_keeps_protected_text_verbatim(store, config):
    engine, client = engine_with(store, config)
    original = "FACT-1 " + "Repeated discussion. " * 3000 + " FACT-99"
    brief = "Never borrow money. Maximum loss: EUR 500."
    pin = store.pin(engine.run_id, "Do not assume interviews were actually conducted.")
    result = await engine.call(
        "RB", "large-input", "Develop a plan", {"brief": brief, "old_discussion": original}
    )
    data = data_from(result["prompt"])
    assert data["brief"] == brief
    assert pin in data["protected_notes"]
    assert "FACT-1" in data["old_discussion"] and "FACT-99" in data["old_discussion"]
    assert len(json.dumps(data, ensure_ascii=False)) <= 8000
    assert "TRUNCATED" not in result["prompt"]
    assert any(row["source"] == original for row in store.compactions(engine.run_id))
    assert any("FACT-99" in item["source"] for item in client.inputs)
    assert all(
        c["brain"] == "MB" and c["provider"] == "openai"
        for c in store.calls(engine.run_id)
        if c["task"].startswith("memory-")
    )


async def test_concurrent_requests_and_restart_reuse_compaction(store, config):
    engine, client = engine_with(store, config)
    source = "FACT-4 " + "Repeated paragraph. " * 300
    left, right = await asyncio.gather(
        engine.memory.compact(source, 1000, "left"), engine.memory.compact(source, 1000, "right")
    )
    assert left == right
    count = len(store.calls(engine.run_id))
    second = Engine(store, engine.run_id, clients=engine.clients)
    assert await second.memory.compact(source, 1000, "reopened") == left
    assert len(store.calls(engine.run_id)) == count
    assert count == len(client.inputs)


async def test_many_rounds_keep_early_findings_specialists_and_critical_objections(
    store, config, tmp_path
):
    engine, _ = engine_with(store, config)
    for number in range(1, 29):
        review = {
            "blocking_issues": ["Demand remains unproven"],
            "dissent": ["The market may be too small"],
            "human_tests": ["Seek paid commitments"],
        }
        store.save_round(
            engine.run_id, number, 0, f"FACT-{number} " + "Old research detail. " * 180, review
        )
        specialist = store.begin_call(
            engine.run_id, f"round-{number}-revision-0-rb-0", "RB", "openai", "fixture"
        )
        store.finish(specialist, "done", {"text": f"FACT-{number + 100} specialist finding"})
        await engine.memory.advance()
    memory = store.memory(engine.run_id)
    assert memory["through_round"] == 28
    for number in range(1, 29):
        assert f"FACT-{number}" in memory["text"]
        assert f"FACT-{number + 100}" in memory["text"]
    assert len(memory["text"]) <= 2000
    assert len(store.pins(engine.run_id)) == 3
    assert "market may be too small" in str(engine.memory.context())
    before = len(store.calls(engine.run_id))
    await engine.memory.advance()
    assert len(store.calls(engine.run_id)) == before
    store.update_run(engine.run_id, brief="Keep this brief")
    # Export code expects the complete review schema.
    for row in store.rounds(engine.run_id):
        review = row["review"] | {
            "score": 70,
            "verdict": "revise",
            "strengths": [],
            "next_prompt": "Validate demand",
        }
        store.save_round(engine.run_id, row["number"], 0, row["plan"], review)
    report = export_run(store, engine.run_id, tmp_path / "export")
    exported = json.loads((report.parent / "history.json").read_text(encoding="utf-8"))
    assert exported["memory"]["through_round"] == 28
    assert exported["compactions"] and exported["memory_pins"]
    assert (report.parent / "memory.md").is_file()


async def test_protected_overflow_pauses_before_any_compaction_or_model_call(store, config):
    engine, _ = engine_with(store, config)
    with pytest.raises(ClientError, match="Protected research context"):
        await engine.call(
            "RB",
            "overflow",
            "Research",
            {"brief": "Important constraint. " * 1000, "details": "More context"},
        )
    assert not store.calls(engine.run_id)


@pytest.mark.parametrize("bad", ["oversize", "source_id", "invented_url"])
async def test_invalid_summary_never_replaces_a_good_checkpoint(store, config, bad):
    engine, _ = engine_with(store, config)
    store.save_memory(engine.run_id, 1, "Existing valid memory")

    class InvalidSummary(DemoClient):
        async def complete(self, prompt, search=False):
            data = data_from(prompt)
            return {
                "text": json.dumps(
                    {
                        "source_id": "wrong" if bad == "source_id" else data["source_id"],
                        "summary": "x" * 10001
                        if bad == "oversize"
                        else "https://invented.invalid"
                        if bad == "invented_url"
                        else "small",
                    }
                ),
                "usage": {},
            }

    engine.clients["openai"] = InvalidSummary()
    with pytest.raises(Halt, match="Memory compaction failed validation"):
        await engine.memory.compact("Research detail. " * 300, 1000, "invalid")
    assert store.memory(engine.run_id)["text"] == "Existing valid memory"
    assert all(row["summary"] is None for row in store.compactions(engine.run_id))
    assert store.calls(engine.run_id)[-1]["state"] == "invalid"


async def test_pause_during_compaction_keeps_round_and_resumes_without_repeating_research(
    store, config
):
    engine, _ = engine_with(store, config)
    config.research.max_context_chars = 8000
    started, cancelled = asyncio.Event(), asyncio.Event()
    store.save_round(
        engine.run_id, 1, 0, "Long plan. " * 1000, {"blocking_issues": ["Important objection"]}
    )

    class SlowSummary(Summarizer):
        async def complete(self, prompt, search=False):
            started.set()
            try:
                await asyncio.sleep(1000)
            finally:
                cancelled.set()

    engine.clients["openai"] = SlowSummary()
    task = asyncio.create_task(engine.run())
    await asyncio.wait_for(started.wait(), 3)
    store.update_run(engine.run_id, control="pause")
    await asyncio.wait_for(task, 3)
    assert cancelled.is_set()
    assert store.run(engine.run_id)["status"] == "paused"
    assert len(store.rounds(engine.run_id)) == 1
    store.update_run(engine.run_id, control="")
    resumed = Engine(
        store, engine.run_id, clients={"openai": Summarizer(), "anthropic": DemoClient()}
    )
    await resumed.memory.advance()
    assert store.memory(engine.run_id)["through_round"] == 1
    assert "Important objection" in str(resumed.memory.context())


def test_released_pins_remain_archived_without_being_reactivated_by_backfill(store, config):
    run_id = store.create_run("Pins", config, demo=True)
    store.save_round(run_id, 1, 0, "Plan", {"dissent": ["An old objection"]})
    pin = store.pins(run_id)[0]
    store.unpin(run_id, pin["id"])
    store.protect_reviews(run_id)
    assert not store.pins(run_id)
    assert store.pins(run_id, active_only=False)[0]["text"] == "An old objection"
    assert size({"brief": "verbatim"}) < 8000


async def test_owner_dialogue_enters_memory_even_when_commands_finish_out_of_order(store, config):
    engine, _ = engine_with(store, config)
    first = store.enqueue(engine.run_id, "ask", "FACT-101 owner question")
    second = store.enqueue(engine.run_id, "steer", "FACT-102 owner direction")
    commands = store.commands(engine.run_id)
    store.answer(engine.run_id, commands[1], "Revised brief")
    await engine.memory.advance()
    assert store.memory(engine.run_id)["commands"] == [second]
    store.answer(engine.run_id, commands[0], "FACT-103 MB answer")
    await engine.memory.advance()
    memory = store.memory(engine.run_id)
    assert set(memory["commands"]) == {first, second}
    assert all(marker in memory["text"] for marker in ("FACT-101", "FACT-102", "FACT-103"))
    await engine.memory.advance()
    assert store.memory(engine.run_id) == memory
