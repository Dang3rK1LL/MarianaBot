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


@pytest.mark.parametrize("bad", ["oversize", "source_id", "invented_url", "empty", "invalid_json"])
@pytest.mark.parametrize("fenced", [False, True])
async def test_invalid_summary_never_replaces_a_good_checkpoint(
    store, config, bad, fenced, tmp_path
):
    engine, _ = engine_with(store, config)
    store.save_memory(engine.run_id, 1, "Existing valid memory")

    class InvalidSummary(DemoClient):
        async def complete(self, prompt, search=False):
            data = data_from(prompt)
            raw = (
                "not JSON"
                if bad == "invalid_json"
                else json.dumps(
                    {
                        "source_id": "wrong" if bad == "source_id" else data["source_id"],
                        "summary": "x" * 10001
                        if bad == "oversize"
                        else "https://invented.invalid"
                        if bad == "invented_url"
                        else "   "
                        if bad == "empty"
                        else "small",
                    }
                )
            )
            return {"text": f"```json\n{raw}\n```" if fenced else raw, "usage": {}}

    engine.clients["openai"] = InvalidSummary()
    with pytest.raises((Halt, ClientError), match="Memory compaction failed validation"):
        await engine.memory.compact("Research detail. " * 300, 1000, "invalid")
    assert store.memory(engine.run_id)["text"] == "Existing valid memory"
    assert all(row["summary"] is None for row in store.compactions(engine.run_id))
    rejected = store.calls(engine.run_id)[-1]
    assert rejected["state"] == "invalid"
    assert rejected["result"]["text"]
    assert "Memory compaction failed validation" in rejected["result"]["validation_error"]
    assert "COMPACT_MEMORY" in rejected["result"]["prompt"]
    assert store.cached(engine.run_id, rejected["task"]) is None
    if bad == "invented_url":
        report = export_run(store, engine.run_id, tmp_path / "rejected-export")
        assert "https://invented.invalid" not in (report.parent / "citations.md").read_text()
        assert "https://invented.invalid" in (report.parent / "history.json").read_text()


@pytest.mark.parametrize("fence", ["json", ""])
async def test_fenced_summary_keeps_source_and_url_validation(store, config, fence):
    engine, _ = engine_with(store, config)
    summary = "FACT-1: test demand. Source: https://evidence.example/report"

    class FencedSummary(DemoClient):
        async def complete(self, prompt, search=False):
            data = data_from(prompt)
            payload = json.dumps({"source_id": data["source_id"], "summary": summary})
            return {"text": f"```{fence}\n{payload}\n```", "usage": {}}

    engine.clients["openai"] = FencedSummary()
    source = "FACT-1: test demand. " * 70 + "https://evidence.example/report"
    assert await engine.memory.compact(source, 1000, "fenced") == summary
    assert len(store.calls(engine.run_id)) == 1
    assert store.calls(engine.run_id)[0]["state"] == "done"
    assert store.compactions(engine.run_id)[0]["source"] == source


async def test_compaction_retry_explains_failure_and_preserves_each_attempt(
    store, config, monkeypatch
):
    engine, _ = engine_with(store, config)
    engine.config.research.max_retries = 2

    async def skip_backoff(*args):
        pass

    monkeypatch.setattr(engine, "wait", skip_backoff)

    class CorrectingSummary(DemoClient):
        def __init__(self):
            self.prompts = []

        async def complete(self, prompt, search=False):
            self.prompts.append(prompt)
            data = data_from(prompt)
            attempt = len(self.prompts)
            return {
                "text": json.dumps(
                    {
                        "source_id": "wrong" if attempt == 1 else data["source_id"],
                        "summary": "x" * 1001 if attempt == 2 else "FACT-1: test demand.",
                    }
                ),
                "usage": {"input_tokens": 100, "output_tokens": 20},
            }

    client = CorrectingSummary()
    engine.clients["openai"] = client
    source = "FACT-1: test demand. " * 70
    assert await engine.memory.compact(source, 1000, "retry") == "FACT-1: test demand."
    calls = store.calls(engine.run_id)
    assert [call["state"] for call in calls] == ["invalid", "invalid", "done"]
    assert "source_id does not match" in client.prompts[1]
    assert "1,000-character target (1,001 characters)" in client.prompts[2]
    assert "source_id does not match" not in client.prompts[2]
    assert [call["result"]["prompt"] for call in calls] == client.prompts
    assert [data_from(value)["source"] for value in client.prompts] == [
        source,
        source,
        "x" * 1001,
    ]
    assert store.usage_totals(engine.run_id)["openai"]["input_tokens"] == 300
    assert await engine.memory.compact(source, 1000, "retry") == "FACT-1: test demand."
    assert len(store.calls(engine.run_id)) == 3


async def test_size_repair_reduces_candidates_and_checkpoints_only_bounded_memory(store, config):
    engine, _ = engine_with(store, config)
    engine.config.research.max_retries = 2
    source = "FACT-1: unproven demand. " * 80 + "https://evidence.example/report"
    candidates = [
        "FACT-1: unproven demand. " * 35 + "https://evidence.example/report",
        "FACT-1: unproven demand. " * 27 + "https://evidence.example/report",
        "FACT-1: unproven demand. Source: https://evidence.example/report",
    ]

    class LengthRepair(DemoClient):
        def __init__(self):
            self.inputs = []

        async def complete(self, prompt, search=False):
            data = data_from(prompt)
            self.inputs.append(data)
            return {
                "text": json.dumps(
                    {"source_id": data["source_id"], "summary": candidates[len(self.inputs) - 1]}
                ),
                "usage": {"input_tokens": 100, "output_tokens": 20},
            }

    client = LengthRepair()
    engine.clients["openai"] = client
    summary = await engine.memory.compact(source, 600, "evidence")
    assert summary == candidates[-1]
    assert [item["source"] for item in client.inputs] == [source, *candidates[:-1]]
    assert all(int(item["target_chars"]) < int(item["max_chars"]) == 600 for item in client.inputs)
    assert all(int(item["target_words"]) <= 45 for item in client.inputs)
    calls = store.calls(engine.run_id)
    assert [call["state"] for call in calls] == ["invalid", "invalid", "done"]
    assert all(store.cached(engine.run_id, call["task"]) is None for call in calls[:-1])
    assert {row["source"] for row in store.compactions(engine.run_id)} == {
        source,
        *candidates[:-1],
    }
    assert all(row["summary"] == summary for row in store.compactions(engine.run_id))
    assert store.usage_totals(engine.run_id)["openai"]["input_tokens"] == 300
    assert await engine.memory.compact(source, 600, "again") == summary
    assert len(client.inputs) == 3


async def test_restart_shortens_saved_oversized_summary_without_repeating_original(store, config):
    engine, _ = engine_with(store, config)
    engine.config.research.max_retries = 1
    source = "FACT-1: test willingness to pay. " * 60
    candidate = "FACT-1: test willingness to pay. " * 25

    class PausingRepair(DemoClient):
        def __init__(self):
            self.inputs = []

        async def complete(self, prompt, search=False):
            data = data_from(prompt)
            self.inputs.append(data)
            if len(self.inputs) > 1:
                raise Halt("paused", "Owner requested pause")
            return {
                "text": json.dumps({"source_id": data["source_id"], "summary": candidate}),
                "usage": {},
            }

    client = PausingRepair()
    engine.clients["openai"] = client
    with pytest.raises(Halt, match="Owner requested pause"):
        await engine.memory.compact(source, 600, "first")
    before = store.calls(engine.run_id)
    assert [call["state"] for call in before] == ["invalid", "unknown"]
    resumed = Engine(store, engine.run_id, clients={"openai": Summarizer()})
    resumed.config.research.max_retries = 1
    summary = await resumed.memory.compact(source, 600, "resume")
    assert "FACT-1" in summary
    assert [item["source"] for item in resumed.clients["openai"].inputs] == [candidate]
    assert store.calls(engine.run_id)[:2] == before
    assert store.calls(engine.run_id)[-1]["task"] == before[-1]["task"]


@pytest.mark.parametrize("shrinks", [False, True])
async def test_size_repair_stops_without_progress_or_when_budget_is_exhausted(
    store, config, shrinks
):
    engine, _ = engine_with(store, config)
    engine.config.research.max_retries = 1
    store.save_memory(engine.run_id, 1, "Good checkpoint")

    class UnboundedSummary(DemoClient):
        def __init__(self):
            self.inputs = []

        async def complete(self, prompt, search=False):
            data = data_from(prompt)
            self.inputs.append(data)
            return {
                "text": json.dumps(
                    {
                        "source_id": data["source_id"],
                        "summary": data["source"][: int(len(data["source"]) * 0.8)]
                        if shrinks
                        else data["source"],
                    }
                ),
                "usage": {},
            }

    client = UnboundedSummary()
    engine.clients["openai"] = client
    with pytest.raises(ClientError, match="size repair limit" if shrinks else "insufficient size"):
        await engine.memory.compact("Long evidence. " * 100, 600, "stalled")
    assert len(client.inputs) == (2 if shrinks else 1)
    assert store.memory(engine.run_id)["text"] == "Good checkpoint"
    assert all(row["summary"] is None for row in store.compactions(engine.run_id))
    assert all(call["state"] == "invalid" for call in store.calls(engine.run_id))


@pytest.mark.parametrize("bad", ["source_id", "invented_url"])
async def test_oversized_candidate_must_pass_identity_and_url_checks_before_repair(
    store, config, bad
):
    engine, _ = engine_with(store, config)
    engine.config.research.max_retries = 1

    async def skip_backoff(*args):
        pass

    engine.wait = skip_backoff

    class InvalidCandidate(DemoClient):
        def __init__(self):
            self.inputs = []

        async def complete(self, prompt, search=False):
            data = data_from(prompt)
            self.inputs.append(data)
            return {
                "text": json.dumps(
                    {
                        "source_id": "wrong" if bad == "source_id" else data["source_id"],
                        "summary": "Long candidate. " * 50 + "https://invented.invalid",
                    }
                ),
                "usage": {},
            }

    client = InvalidCandidate()
    engine.clients["openai"] = client
    source = "Original evidence. " * 100
    for _ in range(2):
        with pytest.raises(Halt, match="source_id does not match|URL absent from the source"):
            await engine.memory.compact(source, 600, "invalid")
    assert len(client.inputs) == 4
    assert all(item["source"] == source for item in client.inputs)
    assert all(row["summary"] is None for row in store.compactions(engine.run_id))


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
