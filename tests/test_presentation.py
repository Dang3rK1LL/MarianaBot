import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from marianabot.clients import ClientError
from marianabot.discord_bridge import usage_snapshot
from marianabot.discord_messages import card_text, recap_card, round_start_card, state_card
from marianabot.engine import Engine
from marianabot.presentation import research_brief, update_metadata


def test_active_round_survives_pause_and_differs_from_completed_checkpoint(store, config):
    run_id = store.create_run("Assess a digital product", config, demo=True)
    store.update_run(run_id, status="paused", round=5)
    store.event(
        run_id, "Round 6 started", kind="round_started", payload={"number": 6, "revision": 0}
    )
    call = store.begin_call(run_id, "memory-source", "MB", "openai", "fixture")
    store.finish(call, "invalid", {"text": "Oversized summary"})
    assert store.progress(run_id) == {"number": 6, "completed": 5, "stage": "memory"}
    notification = card_text(state_card(store.run(run_id), usage_snapshot(store, run_id)))
    assert "Round 6" in notification and "5 rounds saved" in notification
    store.update_run(run_id, round=6)
    assert store.progress(run_id)["number"] == 6
    store.begin_call(run_id, "round-6-revision-0-jb-0", "JB", "anthropic", "fixture")
    assert store.progress(run_id)["stage"] == "critique"
    store.event(run_id, "Next round", kind="round_started", payload={"number": 7, "revision": 0})
    assert store.progress(run_id)["number"] == 7


def test_prompt_templates_never_appear_as_research_briefs():
    prompt = "===== MASTER RESEARCH PROMPT =====\n[SYSTEM ROLE & DIRECTIVE]\nYou are a market intelligence engine."
    run = {
        "problem": prompt,
        "brief": "## Research brief\n**Owner objective:** Assess demand for a paid design archive. Write a full plan later.",
    }
    assert research_brief(run) == "Assess demand for a paid design archive."
    assert "DIRECTIVE" not in research_brief(run | {"brief": ""})
    assert "MASTER" not in research_brief(run | {"brief": ""})


def test_saved_updates_are_separate_from_the_full_memo():
    metadata = {
        "summary": "Published prices support the low-cost option.",
        "changes": ["Removed an ineligible free host."],
        "next_step": "Check official fee rules.",
    }
    text = (
        "The complete memo stays here.\n<research_update>"
        + json.dumps(metadata)
        + "</research_update>"
    )
    memo, update = update_metadata(text)
    assert memo == "The complete memo stays here." and update == metadata
    assert update_metadata("Useful memo <research_update>bad JSON</research_update>")[0].startswith(
        "Useful memo"
    )
    assert update_metadata('<research_update>{"summary":"No memo"}</research_update>')[1] == {}


def test_notifications_show_changes_and_actions_without_prompt_or_plan_excerpts(config):
    run = {
        "id": "MB-FIXTURE",
        "problem": "[SYSTEM ROLE] You are an agent. " * 30,
        "brief": "Research question: Assess demand for a small paid design archive.",
        "config": config.model_dump_json(),
        "demo": False,
    }
    review = {
        "score": 76,
        "verdict": "approve",
        "summary": "Public sales support a one-time kit; subscriptions remain unverified.",
        "changes": ["Replaced the monthly-only offer with a one-time kit."],
        "next_step": "Verify brand availability online.",
        "next_prompt": "MASTER PROMPT " * 200,
        "online_checks": [],
        "blocking_issues": [],
        "limitations": ["Retention is not public."],
    }
    start = round_start_card(
        run,
        {"number": 6, "max_rounds": 24, "previous_review": review, "focus": review["next_prompt"]},
    )
    recap = recap_card(run, {"number": 5, "plan": "Huge raw plan " * 500, "review": review}, {})
    for embed in (start, recap):
        text = card_text(embed)
        assert len(embed["description"]) < 100 and "design archive" in embed["description"]
        assert (
            "MASTER PROMPT" not in text
            and "SYSTEM ROLE" not in text
            and "Huge raw plan" not in text
        )
        assert "Verify brand availability online." in text and len(text) < 1500
        assert text.count("MB-FIXTURE") == 1
    assert "Replaced the monthly-only offer" in card_text(recap)


async def test_transient_usage_check_retries_without_inference_and_keeps_diagnostics(store, config):
    config.research.max_retries = 2
    run_id = store.create_run("Retry metadata safely", config, demo=True)
    engine = Engine(store, run_id)
    failed = ClientError(
        "Codex account/rateLimits/read failed: HTTP 503 unavailable", retryable=True
    )
    failed.diagnostics = {
        "method": "account/rateLimits/read",
        "error": {"code": -32000, "message": "HTTP 503 unavailable"},
    }
    engine.account = SimpleNamespace(
        snapshot=AsyncMock(
            side_effect=[
                failed,
                failed,
                {"limits": {"rateLimits": {"primary": {"usedPercent": 12}}}},
            ]
        )
    )
    engine.wait = AsyncMock()
    await engine.refresh_account()
    assert engine.account.snapshot.await_count == 3 and engine.wait.await_count == 2
    assert store.get_limits("openai")["windows"][0]["percent"] == 12
    assert not store.calls(run_id)
    errors = [event for event in store.events(run_id) if event["kind"] == "account_check_failed"]
    assert (
        len(errors) == 2 and json.loads(errors[0]["payload"])["diagnostics"] == failed.diagnostics
    )


async def test_usage_outage_exhaustion_keeps_saved_limits_and_sends_no_research(store, config):
    config.research.max_retries = 1
    run_id = store.create_run("Do not bypass account checks", config, demo=True)
    store.set_limits("openai", {"observed": 123, "windows": [{"percent": 25}]})
    engine = Engine(store, run_id)
    engine.demo = False
    engine.account = SimpleNamespace(
        snapshot=AsyncMock(side_effect=ClientError("Usage service HTTP 503", retryable=True))
    )
    engine.wait = AsyncMock()
    with pytest.raises(ClientError, match="after 2 usage-check attempts"):
        await engine.capacity("openai")
    assert not store.calls(run_id)
    assert store.get_limits("openai")["observed"] == 123


async def test_new_memo_metadata_is_saved_without_an_extra_model_call(store, config):
    run_id = store.create_run("Summarize changes in the research call", config, demo=True)
    metadata = {
        "summary": "Compared official fees.",
        "changes": ["Corrected the fixed cost."],
        "next_step": "Check pricing.",
    }
    client = SimpleNamespace(
        complete=AsyncMock(
            return_value={
                "text": "Useful full memo.\n<research_update>"
                + json.dumps(metadata)
                + "</research_update>",
                "usage": {},
            }
        )
    )
    result = await Engine(store, run_id, clients={"openai": client}).call(
        "RB", "round-1-revision-0-synthesis", "Synthesize", {}
    )
    assert result["text"] == "Useful full memo." and result["update"] == metadata
    assert client.complete.await_count == 1 and len(store.calls(run_id)) == 1
