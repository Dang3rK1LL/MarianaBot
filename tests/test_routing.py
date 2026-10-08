import asyncio
import json

import pytest
from pydantic import ValidationError

from marianabot.clients import DemoClient, NativeClient
from marianabot.config import Config
from marianabot.engine import Engine, stop_reason
from marianabot.model_catalog import ModelOption, parse_models
from marianabot.prompts import Review
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


async def test_overlong_research_is_rejected_and_retried_with_the_configured_ceiling(
    store, config, monkeypatch
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

    async def complete(client, content, search=False, **kwargs):
        selections.append((client.config.rb.model, client.config.rb.effort))
        return {
            "text": "Unnecessary detail. " * 200
            if len(selections) == 1
            else "A concise evidence-based conclusion.",
            "usage": {},
        }

    async def no_wait(*args):
        pass

    monkeypatch.setattr(NativeClient, "complete", complete)
    engine.wait = no_wait
    result = await engine.call("RB", "round-1-revision-0-rb-0", "Research", {})
    assert result["text"] == "A concise evidence-based conclusion."
    assert selections == [("gpt-6.1-sol", "medium"), (config.rb.model, "high")]
    assert [row["state"] for row in store.calls(run_id)] == ["invalid", "done"]
    assert store.cached(run_id, "round-1-revision-0-rb-0")["text"] == result["text"]
