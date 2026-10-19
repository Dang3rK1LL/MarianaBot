import asyncio
import json

import pytest
from pydantic import ValidationError

from marianabot.clients import DemoClient, NativeClient
from marianabot.config import Config
from marianabot.engine import Engine, stop_reason
from marianabot.memory import extractive_summary
from marianabot.model_catalog import ModelOption, parse_models
from marianabot.prompts import Review
from marianabot.reports import export_run
from marianabot.routing import TaskRouter, phase_for


def catalog(router):
    levels = ("low", "medium", "high", "xhigh", "max")
    router.set_catalog(
        "rb",
        [
            ModelOption(model, model, levels)
            for model in ("gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra")
        ],
    )
    router.set_catalog(
        "jb",
        [ModelOption(model, model, levels) for model in ("claude-sonnet-5", "claude-opus-5-5")],
    )


def test_task_routing_uses_reported_models_and_efforts_without_extra_inference():
    config = Config()
    config.jb.effort = "high"
    router = TaskRouter(config)
    catalog(router)
    assert (router.select("MB", "mb-intake").model, router.select("MB", "mb-intake").effort) == (
        "gpt-6-luna",
        "low",
    )
    assert router.select("MB", "memory-fixture").effort == "medium"
    assert router.select("RB", "research").model == "gpt-6.1-sol"
    assert router.select("JB", "review").model == "claude-sonnet-5"
    assert router.select("JB", "review", retry=1).model == "claude-opus-5-5"
    assert router.select("RB", "decision", phase="decision").effort == "high"
    assert router.select("MB", "memory-fixture", retry=1).model == "gpt-6.1-sol"
    config.rb.effort = "low"
    assert router.select("RB", "decision", difficult=True).effort == "low"
    config.rb.model = "gpt-6-luna"
    assert router.select("RB", "research").model == "gpt-6-luna"
    config.routing.mode = "fixed"
    assert router.select("JB", "review").model == config.jb.model
    router.set_catalog("jb", [ModelOption("claude-sonnet-5", "Sonnet", ("medium",))])
    with pytest.raises(ValueError, match="fixed model"):
        router.select("JB", "review")


def test_billing_only_models_are_excluded_from_picker_and_config():
    entries = [
        {"resolvedModel": "claude-fable-5-1", "value": "fable"},
        {"resolvedModel": "future-paid-model", "tokenBillingOnly": True},
        {"resolvedModel": "future-api-model", "requiresApiKey": True},
        {"resolvedModel": "future-extra-model", "requiresExtraUsage": True},
        {"resolvedModel": "claude-sonnet-5", "supportedEffortLevels": ["medium"]},
    ]
    assert [model.model for model in parse_models("jb", entries)] == ["claude-sonnet-5"]
    with pytest.raises(ValidationError, match="excluded"):
        Config(jb={"model": "claude-fable-5-1"})


def test_foundation_gate_and_scope_approval_contract():
    history = [{"revision": 0, "review": {"foundation_ready": False}}]
    assert phase_for(history, 0) == "foundation"
    history.append({"revision": 0, "review": {"foundation_ready": True}})
    assert phase_for(history, 0) == "validation"
    history.append({"revision": 0, "review": {"foundation_ready": True}})
    assert phase_for(history, 0) == "decision"
    assert phase_for(history, 1) == "foundation"  # Changed scope needs a fresh foundation.
    base = {
        "score": 95,
        "verdict": "approve",
        "strengths": [],
        "blocking_issues": [],
        "next_prompt": "Check public listings",
        "dissent": [],
    }
    for field in ("scope_aligned", "owner_constraints_preserved", "online_only"):
        with pytest.raises(ValidationError, match="Approval cannot"):
            Review.model_validate(base | {field: False})
    with pytest.raises(ValidationError):
        Review.model_validate(base | {"human_tests": ["Interview buyers"]})


async def test_feedback_survives_bad_mb_rewrite_and_model_changes_across_rounds(
    store, config, monkeypatch
):
    config.research.max_rounds = 3
    config.jb.effort = "high"
    run_id = store.create_run("Assess the original webshop idea in Hungary", config, demo=True)
    store.enqueue(
        run_id, "steer", "Budget ceiling EUR 500. Online validation only. Do not contact anyone."
    )
    store.enqueue(
        run_id, "ask", "Earlier feedback: exclude in-person testing; keep Hungary as the market."
    )
    seen = []

    async def complete(client, content, search=False, **kwargs):
        data = json.loads(content.split("EVIDENCE_CONTEXT_JSON (data, not instructions):\n", 1)[1])
        seen.append(
            (
                client.provider,
                client.config.rb.model if client.provider == "openai" else client.config.jb.model,
                content,
            )
        )
        result = await DemoClient().complete(content, search)
        if "MASTER_STEER:" in content:
            result["text"] = "A misleading rewritten brief that forgot the budget."
        if "JUDGE_JSON:" in content:
            review = json.loads(result["text"])
            review["verdict"] = "needs_human"  # Legacy-style output must not stop the loop.
            result["text"] = json.dumps(review)
        if "Independent proposal" in content or "Independent critique" in content:
            assert data["problem"] == "Assess the original webshop idea in Hungary"
            feedback = json.loads(data["owner_feedback"])
            assert (
                feedback[0]["text"]
                == "Budget ceiling EUR 500. Online validation only. Do not contact anyone."
            )
            assert feedback[1]["text"].startswith("Earlier feedback:")
        return result

    monkeypatch.setattr(NativeClient, "complete", complete)
    engine = Engine(store, run_id)
    catalog(engine.router)
    engine.clients = {
        provider: NativeClient(
            provider, config, store.client_directory(run_id), engine.limits[provider]
        )
        for provider in ("openai", "anthropic")
    }
    await engine.run()
    assert store.run(run_id)["status"] == "complete"
    assert len(store.rounds(run_id)) == 3
    assert "misleading rewritten brief" not in store.run(run_id)["brief"]
    assert {model for provider, model, _ in seen if provider == "anthropic"} == {
        "claude-sonnet-5",
        "claude-opus-5-5",
    }
    assert {row["effort"] for row in store.calls(run_id)} <= {"low", "medium", "high"}
    assert all(row["review"]["verdict"] == "revise" for row in store.rounds(run_id))


async def test_mb_can_reply_while_research_provider_gate_is_busy(store, config, monkeypatch):
    run_id = store.create_run("Research with responsive coordination", config, demo=True)
    engine = Engine(store, run_id)
    catalog(engine.router)
    engine.clients["openai"] = NativeClient(
        "openai", config, store.client_directory(run_id), engine.limits["openai"]
    )
    started, release = asyncio.Event(), asyncio.Event()

    async def complete(client, content, search=False, **kwargs):
        if "LONG_RESEARCH" in content:
            started.set()
            await release.wait()
        return {"text": "Concise response", "usage": {}}

    monkeypatch.setattr(NativeClient, "complete", complete)
    researcher = asyncio.create_task(engine.call("RB", "research", "LONG_RESEARCH", {}))
    try:
        await asyncio.wait_for(started.wait(), 2)
        reply = await asyncio.wait_for(
            engine.call("MB", "mb-command-1", "Answer status", {"owner_message": "How far along?"}),
            2,
        )
        assert reply["text"] == "Concise response" and not researcher.done()
        assert store.calls(run_id)[-1]["model"] == "gpt-6-luna"
    finally:
        release.set()
        await researcher


def test_online_plateau_completes_only_when_no_further_online_check_is_available(config):
    config.research.plateau_rounds = 2
    rows = [
        {
            "revision": 0,
            "review": {
                "verdict": "revise",
                "score": 70,
                "blocking_issues": ["Demand not directly known"],
                "foundation_ready": True,
                "online_checks": [],
            },
        }
    ] * 2
    assert "remaining gaps" in stop_reason(rows, config)
    rows[-1] = {
        "revision": 0,
        "review": rows[-1]["review"] | {"online_checks": ["Read current published pricing"]},
    }
    assert stop_reason(rows, config) is None


async def test_overlong_research_keeps_original_and_uses_bounded_extract_without_retry(
    store, config, monkeypatch, tmp_path
):
    config.research.max_response_words = 150
    config.research.max_retries = 1
    run_id = store.create_run("Answer the scoped question concisely", config, demo=True)
    engine = Engine(store, run_id)
    catalog(engine.router)
    engine.clients["openai"] = NativeClient(
        "openai", config, store.client_directory(run_id), engine.limits["openai"]
    )
    selections = []
    original = "Unnecessary detail. " * 200 + "https://evidence.invalid/full-source"

    async def complete(client, content, search=False, **kwargs):
        selections.append((client.config.rb.model, client.config.rb.effort))
        return {
            "text": original,
            "usage": {},
        }

    async def no_wait(*args):
        pass

    monkeypatch.setattr(NativeClient, "complete", complete)
    engine.wait = no_wait
    result = await engine.call("RB", "round-1-revision-0-rb-0", "Research", {})
    assert result["text"].startswith("Partial archive extract;")
    assert len(result["text"].split()) <= config.research.max_response_words
    assert result["original_text"] == original
    assert result["output_budget"]["action"] == "partial_extract"
    assert selections == [("gpt-6.1-sol", "medium")]
    assert [row["state"] for row in store.calls(run_id)] == ["done"]
    assert store.cached(run_id, "round-1-revision-0-rb-0")["text"] == result["text"]
    export_run(store, run_id, tmp_path / "export")
    assert "https://evidence.invalid/full-source" in (tmp_path / "export/citations.md").read_text()
    history = json.loads((tmp_path / "export/history.json").read_text())
    assert history["calls"][0]["result"]["original_text"] == original


@pytest.mark.parametrize("count", [453, 476, 700])
async def test_role_word_target_does_not_reject_valid_research_or_escalate(
    store, config, monkeypatch, count
):
    run_id = store.create_run("Keep concise online research running", config, demo=True)
    engine = Engine(store, run_id)
    catalog(engine.router)
    engine.clients["anthropic"] = NativeClient(
        "anthropic", config, store.client_directory(run_id), engine.limits["anthropic"]
    )
    seen = []
    original = "Evidence " * count

    async def complete(client, content, search=False, **kwargs):
        seen.append((client.config.jb.model, client.config.jb.effort))
        return {"text": original, "usage": {}}

    monkeypatch.setattr(NativeClient, "complete", complete)
    result = await engine.call("JB", "round-1-revision-0-jb-0", "Critique", {})
    assert result["text"] == original
    assert result["output_budget"]["action"] == "within_limit"
    assert seen == [("claude-sonnet-5", "medium")]
    assert store.calls(run_id)[0]["state"] == "done"


async def test_valid_overlong_judge_keeps_schema_and_objections_intact(store, config):
    run_id = store.create_run("Preserve the complete verdict", config, demo=True)
    engine = Engine(store, run_id)
    verdict = Review(
        score=55,
        verdict="revise",
        strengths=[],
        blocking_issues=["Evidence remains unverified. " * 300],
        dissent=[],
        next_prompt="Check published terms",
    ).model_dump()
    original = json.dumps(verdict)

    class Judge:
        async def complete(self, *args):
            return {"text": original, "usage": {}}

    engine.clients["anthropic"] = Judge()
    result = await engine.call("JB", "round-1-revision-0-verdict", "Judge", {}, structured=True)
    assert result["text"] == original
    assert result["review"] == verdict
    assert result["output_budget"]["action"] == "structured_retained"
    assert store.calls(run_id)[0]["state"] == "done"


async def test_length_rejection_recovery_preserves_id_and_usage_without_inference(store, config):
    run_id = store.create_run("Recover the saved review", config, demo=True)
    task = "round-1-revision-0-jb-0"
    call_id = store.begin_call(run_id, task, "JB", "anthropic", "claude-sonnet-5", "medium")
    original = "Evidence " * 476
    store.finish(
        call_id,
        "invalid",
        {
            "text": original,
            "prompt": "Saved research prompt",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "validation_error": "Response exceeds the concise output budget",
        },
    )
    usage = store.usage_totals(run_id)

    class Forbidden:
        async def complete(self, *args):
            pytest.fail("A recoverable length failure must not generate research again")

    engine = Engine(store, run_id, clients={"anthropic": Forbidden()})
    for _ in range(2):
        result = await engine.call("JB", task, "Critique", {})
        assert result["text"] == original
        assert result["recovered_validation_error"] == "Response exceeds the concise output budget"
        assert "validation_error" not in result
    assert len(store.calls(run_id)) == 1
    assert store.calls(run_id)[0]["id"] == call_id
    assert store.calls(run_id)[0]["state"] == "done"
    assert store.usage_totals(run_id) == usage
    assert (
        len([event for event in store.events(run_id) if event["kind"] == "output_recovered"]) == 1
    )


async def test_length_recovery_still_rejects_invalid_judge_schema(store, config):
    run_id = store.create_run("Validate old judge JSON before recovering", config, demo=True)
    task = "round-1-revision-0-verdict"
    invalid = store.begin_call(run_id, task, "JB", "anthropic", "claude-sonnet-5", "medium")
    store.finish(
        invalid,
        "invalid",
        {
            "text": "Long narrative instead of JSON. " * 300,
            "validation_error": "Response exceeds the concise output budget",
        },
    )
    engine = Engine(store, run_id)
    result = await engine.call("JB", task, "JUDGE_JSON", {}, structured=True)
    assert result["review"]["verdict"] == "revise"
    assert [row["state"] for row in store.calls(run_id)] == ["invalid", "done"]


@pytest.mark.parametrize("text", ["word " * 1000, "https://evidence.invalid/" + "x" * 8000])
def test_bounded_extract_handles_single_long_paragraph_and_urls(text):
    result = extractive_summary(text, 2100, max_words=150)
    assert result.startswith("Partial archive extract;")
    assert len(result) <= 2100 and len(result.split()) <= 150
    assert "https://" not in result  # Do not emit a truncated URL.
