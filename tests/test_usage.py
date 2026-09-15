import asyncio
import contextlib
import json
import sys
import time

import pytest

from marianabot import clients
from marianabot.clients import ClientError, NativeClient
from marianabot.engine import Engine
from marianabot.limits import SubscriptionLimits
from marianabot.store import Store
from marianabot.usage import UsageStream, normalize_usage
from marianabot.usage_ui import quota_line


def start(message_id, usage):
    return {
        "type": "stream_event",
        "event": {"type": "message_start", "message": {"id": message_id, "usage": usage}},
    }


def delta(output):
    return {
        "type": "stream_event",
        "event": {"type": "message_delta", "usage": {"output_tokens": output}},
    }


def test_cached_input_accounting_and_unknown_counts():
    assert normalize_usage(
        "openai", {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 9}
    ) == dict(input_tokens=100, output_tokens=9, cache_read_tokens=80, cache_write_tokens=0)
    assert normalize_usage(
        "anthropic",
        {"input_tokens": 100, "cache_read_input_tokens": 80, "cache_creation_input_tokens": 20},
    ) == dict(input_tokens=200, output_tokens=None, cache_read_tokens=80, cache_write_tokens=20)
    assert normalize_usage("openai", {"input_tokens": True, "output_tokens": -10}) is None
    assert normalize_usage("anthropic", {"input_tokens": float("inf")}) is None
    assert normalize_usage("openai", {}) is None


def test_claude_stream_deduplicates_blocks_and_reconciles_final_total():
    stream = UsageStream("anthropic")
    usage = {"input_tokens": 100, "cache_read_input_tokens": 400, "output_tokens": 1}
    assert stream.observe(start("m1", usage))[0]["input_tokens"] == 500
    assert stream.latest["output_tokens"] is None  # Ignore placeholder output counts.
    stream.observe(delta(7))
    assert stream.observe(delta(7)) is None
    stream.observe(delta(12))  # cumulative, not 7 + 12
    repeated = {"type": "assistant", "message": {"id": "m1", "usage": usage}}
    stream.observe(repeated)
    stream.observe(repeated)
    assert stream.latest["input_tokens"] == 500
    assert stream.latest["output_tokens"] == 12
    stream.observe(start("m2", {"input_tokens": 50, "output_tokens": 1}))
    stream.observe(delta(8))
    assert stream.latest["input_tokens"] == 550
    assert stream.latest["output_tokens"] == 20
    final = {
        "type": "result",
        "subtype": "success",
        "usage": {"input_tokens": 150, "cache_read_input_tokens": 400, "output_tokens": 21},
    }
    assert stream.observe(final) == (
        dict(input_tokens=550, output_tokens=21, cache_read_tokens=400, cache_write_tokens=0),
        True,
    )
    assert stream.observe(repeated) is None


def test_claude_crash_does_not_erase_reported_usage_and_model_totals_include_helpers():
    stream = UsageStream("anthropic")
    stream.observe(start("m1", {"input_tokens": 30}))
    stream.observe(delta(9))
    stream.observe(
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    )
    assert stream.latest["input_tokens"] == 30
    assert stream.latest["output_tokens"] == 9
    assert not stream.final
    stream.observe(
        {
            "type": "result",
            "subtype": "success",
            "usage": {"input_tokens": 30, "output_tokens": 9},
            "modelUsage": {
                "opus": {"inputTokens": 30, "outputTokens": 9, "cacheReadInputTokens": 100},
                "helper": {"inputTokens": 10, "outputTokens": 5},
            },
        }
    )
    assert stream.latest["input_tokens"] == 140
    assert stream.latest["output_tokens"] == 14


def test_usage_survives_failures_resume_and_old_store_migration(store, config):
    run_id = store.create_run("Usage accounting", config, demo=True)
    first = store.begin_call(run_id, "mb", "MB", "openai", config.rb.model)
    store.finish(
        first,
        "done",
        {"usage": {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 10}},
    )
    second = store.begin_call(run_id, "rb", "RB", "openai", config.rb.model)
    store.record_usage(second, normalize_usage("openai", {"input_tokens": 25, "output_tokens": 5}))
    store.finish(second, "unknown")
    third = store.begin_call(run_id, "rb-retry", "RB", "openai", config.rb.model)
    store.finish(third, "done", {"usage": {"input_tokens": 25, "output_tokens": 6}})
    totals = store.usage_totals(run_id)["openai"]
    assert (totals["input_tokens"], totals["output_tokens"], totals["incomplete"]) == (150, 21, 1)
    assert totals["cache_read_tokens"] == 60
    assert store.cached(run_id, "mb")  # Looking up a cached result never increments tokens.
    assert store.usage_totals(run_id) == {"openai": totals}
    with store.db:
        store.db.execute("DELETE FROM call_usage WHERE call_id=?", (first,))
    restored = Store(store.directory)
    try:
        assert restored.usage_totals(run_id)["openai"]["input_tokens"] == 150
        assert restored.usage_totals(run_id)["openai"]["output_tokens"] == 21
    finally:
        restored.close()


def test_usage_final_report_survives_late_partials_and_incomplete_fields_stay_partial(
    store, config
):
    run_id = store.create_run("Uncertain totals", config, demo=True)
    call_id = store.begin_call(run_id, "rb", "RB", "openai", config.rb.model)
    store.record_usage(call_id, normalize_usage("openai", {"input_tokens": 100}), final=True)
    store.record_usage(call_id, normalize_usage("openai", {"input_tokens": 10}))
    row = store.usage_totals(run_id)["openai"]
    assert row["input_tokens"] == 100
    assert row["incomplete"] == 1
    assert row["output_reports"] == 0


def test_quota_display_preserves_stale_windows_and_waits_without_inventing_capacity():
    data = {
        "observed": 990,
        "windows": [
            {"name": "five_hour", "percent": 90, "reset": 999, "observed": 900},
            {"name": "seven_day", "percent": 20, "reset": 5000, "observed": 990},
        ],
    }
    line = quota_line(data, now=1000).plain
    assert "5h 90%" in line and "7d 20%" in line
    assert "refresh due" in line
    assert "seen 1m ago" in line
    assert "not reported" in quota_line({}, now=1000).plain
    assert "waiting 5m" in quota_line({"until": 1300}, now=1000).plain
    assert "no subscription usage" in quota_line(data, demo=True).plain


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_native_usage_is_persisted_before_the_child_exits(
    provider, store, config, tmp_path, monkeypatch
):
    marker = tmp_path / "allow-exit"
    script = tmp_path / "stream.py"
    events = (
        [
            {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 7}},
        ]
        if provider == "openai"
        else [start("m1", {"input_tokens": 10}), delta(7)]
    )
    script.write_text(
        "import sys,json,time\nfrom pathlib import Path\nsys.stdin.read()\n"
        + "\n".join(f"print({json.dumps(json.dumps(e))}, flush=True)" for e in events)
        + "\nwhile not Path(sys.argv[1]).exists(): time.sleep(0.01)\n"
        + (
            "print("
            + repr(
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "result": "ok",
                        "usage": {"input_tokens": 10, "output_tokens": 8},
                    }
                )
            )
            + ", flush=True)\n"
            if provider == "anthropic"
            else ""
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(clients, "executable", lambda _: [sys.executable, str(script), str(marker)])
    run_id = store.create_run("Live events, offline subprocess", config, demo=True)
    engine = Engine(store, run_id)
    engine.clients[provider] = NativeClient(
        provider, config, store.directory, SubscriptionLimits(store, provider, config.subscription)
    )
    task = asyncio.create_task(
        engine.call("JB" if provider == "anthropic" else "RB", "stream", "Test", {})
    )
    try:
        async with asyncio.timeout(8):
            while store.usage_totals(run_id).get(provider, {}).get("output_tokens") != 7:
                await asyncio.sleep(0.01)
        assert not task.done()
        assert store.usage_totals(run_id)[provider]["active"] == 1
        marker.touch()
        await asyncio.wait_for(task, 5)
        assert store.usage_totals(run_id)[provider]["input_tokens"] == 10
        assert store.usage_totals(run_id)[provider]["output_tokens"] == (
            8 if provider == "anthropic" else 7
        )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_quota_monitor_refreshes_without_model_calls_and_keeps_last_good_snapshot(
    store, config
):
    run_id = store.create_run("Monitor while working", config)
    engine = Engine(store, run_id)
    observed = asyncio.Event()

    async def snapshot(include_models=False):
        observed.set()
        return {
            "limits": {
                "rateLimits": {"primary": {"usedPercent": 42, "resetsAt": time.time() + 3600}}
            }
        }

    engine.account.snapshot = snapshot
    task = asyncio.create_task(engine.monitor_usage(interval=0.01))
    try:
        await asyncio.wait_for(observed.wait(), 2)
        assert store.get_limits("openai")["windows"][0]["percent"] == 42
        assert not store.calls(run_id)
        saved = store.get_limits("openai")

        async def unavailable(include_models=False):
            raise ClientError("Unavailable")

        engine.account.snapshot = unavailable
        await asyncio.sleep(0.04)
        assert store.get_limits("openai") == saved
        assert not task.done()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
