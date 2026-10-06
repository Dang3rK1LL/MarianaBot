import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from marianabot.chat import Composer, MarianaChat
from marianabot.clients import ClientError
from marianabot.config import DEFAULT_TOML


def quota(percent):
    return {
        "limits": {
            "rateLimits": {
                "primary": {
                    "usedPercent": percent,
                    "resetsAt": time.time() + 3600,
                    "windowDurationMins": 300,
                }
            }
        }
    }


def claude_quota(percent):
    return {
        "limits": {
            "five_hour": {"utilization": percent, "resets_at": time.time() + 3600},
            "seven_day": {"utilization": 8, "resets_at": time.time() + 86400},
        }
    }


def app_at(tmp_path, *, demo=False):
    config = tmp_path / "mariana.toml"
    config.write_text(DEFAULT_TOML, encoding="utf-8")
    return MarianaChat(
        tmp_path / "state", config, demo=demo, manager=SimpleNamespace(active=lambda: None)
    )


def account_fixture(monkeypatch, *, provider="openai", **kwargs):
    snapshot = AsyncMock(**kwargs)
    account = Mock(return_value=SimpleNamespace(snapshot=snapshot))
    name = "CodexAccount" if provider == "openai" else "ClaudeAccount"
    monkeypatch.setattr("marianabot.chat." + name, account)
    return snapshot


async def until(predicate, pilot):
    async with asyncio.timeout(4):
        while not predicate():
            await pilot.pause(0.02)


async def test_each_application_start_refreshes_even_a_recent_saved_snapshot(tmp_path, monkeypatch):
    snapshot = account_fixture(monkeypatch, side_effect=[quota(35), quota(47)])
    app = app_at(tmp_path)
    app.store.set_limits(
        "openai", dict(observed=time.time(), windows=[dict(name="codex primary", percent=98)])
    )
    async with app.run_test() as pilot:
        await until(lambda: not app.usage_refreshing, pilot)
        assert app.store.get_limits("openai")["windows"][0]["percent"] == 35
        assert not app.store.runs()
    reopened = app_at(tmp_path)
    async with reopened.run_test() as pilot:
        await until(lambda: not reopened.usage_refreshing, pilot)
        assert reopened.store.get_limits("openai")["windows"][0]["percent"] == 47
        assert not reopened.store.runs()
    assert snapshot.await_count == 2
    assert all(call.args == () and call.kwargs == {} for call in snapshot.await_args_list)


async def test_startup_stays_editable_while_fetching_and_coalesces_refresh_requests(
    tmp_path, monkeypatch
):
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_snapshot():
        started.set()
        await release.wait()
        return quota(32)

    snapshot = account_fixture(monkeypatch, side_effect=slow_snapshot)
    app = app_at(tmp_path)
    async with app.run_test() as pilot:
        await asyncio.wait_for(started.wait(), 2)
        app.query_one(Composer).load_text("Keep this unsent problem")
        assert app.query_one(Composer).text == "Keep this unsent problem"
        assert "refreshing" in str(app.query_one("#limit-openai").content)
        app.request_usage_refresh()
        app.request_usage_refresh()
        assert snapshot.await_count == 1
        release.set()
        await until(lambda: not app.usage_refreshing, pilot)
        assert "32%" in str(app.query_one("#limit-openai").content)
        assert not app.store.runs()
        assert app.query_one(Composer).text == "Keep this unsent problem"


async def test_usage_command_refreshes_without_creating_research(tmp_path, monkeypatch):
    snapshot = account_fixture(monkeypatch, side_effect=[quota(10), quota(20)])
    app = app_at(tmp_path)
    async with app.run_test() as pilot:
        await until(lambda: not app.usage_refreshing, pilot)
        await app.command("/usage", "")
        assert snapshot.await_count == 2
        assert "20% used" in app.usage_text()
        assert not app.store.runs()


async def test_failed_startup_keeps_last_good_snapshot_and_manual_retry_recovers(
    tmp_path, monkeypatch
):
    snapshot = account_fixture(monkeypatch, side_effect=[ClientError("Unavailable"), quota(24)])
    app = app_at(tmp_path)
    saved = dict(observed=100, windows=[dict(name="codex primary", percent=54, observed=100)])
    app.store.set_limits("openai", saved)
    async with app.run_test() as pilot:
        await until(lambda: not app.usage_refreshing, pilot)
        assert app.store.get_limits("openai") == saved
        assert "refresh failed" in str(app.query_one("#limit-openai").content)
        assert "last reported snapshot" in app.usage_text()
        await app.command("/usage", "")
        assert app.store.get_limits("openai")["windows"][0]["percent"] == 24
        assert not app.usage_errors["openai"]
        assert "refresh failed" not in str(app.query_one("#limit-openai").content)
        assert snapshot.await_count == 2


async def test_periodic_refresh_runs_while_idle(tmp_path, monkeypatch):
    snapshot = account_fixture(monkeypatch, return_value=quota(42))
    app = app_at(tmp_path)
    app.USAGE_REFRESH_SECONDS = 0.1
    async with app.run_test() as pilot:
        await until(lambda: snapshot.await_count >= 2, pilot)
        assert app.store.get_limits("openai")["windows"][0]["percent"] == 42
        assert not app.store.runs()


async def test_cloud_reconnect_marker_refreshes_even_when_snapshot_is_fresh(tmp_path, monkeypatch):
    snapshot = account_fixture(monkeypatch, side_effect=[quota(12), quota(26)])
    app = app_at(tmp_path)
    async with app.run_test() as pilot:
        await until(lambda: app.usage_worker.is_finished, pilot)
        app.usage_request_path.touch()
        await until(lambda: snapshot.await_count == 2 and not app.usage_refreshing, pilot)
        assert app.store.get_limits("openai")["windows"][0]["percent"] == 26
        assert not app.store.runs()


async def test_offline_demo_does_not_query_account_limits(tmp_path, monkeypatch):
    snapshot = account_fixture(monkeypatch, side_effect=AssertionError("Demo must stay offline"))
    claude = account_fixture(
        monkeypatch, provider="anthropic", side_effect=AssertionError("Demo must stay offline")
    )
    app = app_at(tmp_path, demo=True)
    app.USAGE_REFRESH_SECONDS = 0.1
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        app.usage_request_path.touch()
        await app.refresh_state()
        await app.command("/usage", "")
        assert "no subscription usage" in app.usage_text()
        snapshot.assert_not_awaited()
        claude.assert_not_awaited()


@pytest.mark.parametrize("trigger", ["reopen", "manual", "reconnect", "periodic"])
async def test_claude_refreshes_on_start_and_followup_without_research(
    tmp_path, monkeypatch, trigger
):
    readings = iter([claude_quota(35), claude_quota(47)])
    snapshot = account_fixture(
        monkeypatch, provider="anthropic", side_effect=lambda: next(readings, claude_quota(47))
    )
    account_fixture(monkeypatch, return_value=quota(10))
    app = app_at(tmp_path)
    app.store.set_limits(
        "anthropic", dict(observed=time.time(), windows=[dict(name="five_hour", percent=98)])
    )
    async with app.run_test() as pilot:
        await until(lambda: app.usage_worker.is_finished, pilot)
        assert app.store.get_limits("anthropic")["windows"][0]["percent"] == 35
        assert "refresh failed" not in str(app.query_one("#limit-anthropic").content)
        assert not app.store.runs()
        if trigger == "manual":
            await app.command("/usage", "")
        elif trigger == "reconnect":
            app.usage_request_path.touch()
        elif trigger == "periodic":
            app.set_interval(0.1, app.request_usage_refresh, repeat=1)
        if trigger != "reopen":
            await until(lambda: snapshot.await_count >= 2 and not app.usage_refreshing, pilot)
            assert app.store.get_limits("anthropic")["windows"][0]["percent"] == 47
            assert not app.store.runs()
    if trigger == "reopen":
        reopened = app_at(tmp_path)
        async with reopened.run_test() as pilot:
            await until(lambda: not reopened.usage_refreshing, pilot)
            assert reopened.store.get_limits("anthropic")["windows"][0]["percent"] == 47
    assert snapshot.await_count >= 2 if trigger == "periodic" else snapshot.await_count == 2
    assert all(call.args == () and call.kwargs == {} for call in snapshot.await_args_list)


async def test_claude_failure_does_not_block_codex_and_retry_recovers(tmp_path, monkeypatch):
    account_fixture(monkeypatch, return_value=quota(42))
    snapshot = account_fixture(
        monkeypatch,
        provider="anthropic",
        side_effect=[ClientError("Unavailable"), claude_quota(24)],
    )
    app = app_at(tmp_path)
    saved = dict(observed=100, windows=[dict(name="five_hour", percent=54, observed=100)])
    app.store.set_limits("anthropic", saved)
    async with app.run_test() as pilot:
        await until(lambda: not app.usage_refreshing, pilot)
        assert app.store.get_limits("anthropic") == saved
        assert app.store.get_limits("openai")["windows"][0]["percent"] == 42
        assert "refresh failed" in str(app.query_one("#limit-anthropic").content)
        assert "refresh failed" not in str(app.query_one("#limit-openai").content)
        await app.command("/usage", "")
        assert not app.usage_error
        assert app.store.get_limits("anthropic")["windows"][0]["percent"] == 24
        assert snapshot.await_count == 2


async def test_slow_claude_refresh_keeps_codex_visible_and_quit_cancels_it(tmp_path, monkeypatch):
    account_fixture(monkeypatch, return_value=quota(42))
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def pending():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    snapshot = account_fixture(monkeypatch, provider="anthropic", side_effect=pending)
    app = app_at(tmp_path)
    async with app.run_test() as pilot:
        await until(lambda: bool(app.store.get_limits("openai").get("windows")), pilot)
        assert started.is_set()
        app.query_one(Composer).load_text("An unsent draft")
        app.request_usage_refresh()
        await pilot.pause()
        assert snapshot.await_count == 1
        assert "42%" in str(app.query_one("#limit-openai").content)
        assert "refreshing" not in str(app.query_one("#limit-openai").content)
        assert "refreshing" in str(app.query_one("#limit-anthropic").content)
        assert app.query_one(Composer).text == "An unsent draft"
        app.action_detach()
    assert cancelled.is_set()


async def test_quitting_cancels_pending_account_refresh(tmp_path, monkeypatch):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def pending_snapshot():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    account_fixture(monkeypatch, side_effect=pending_snapshot)
    app = app_at(tmp_path)
    async with app.run_test():
        await asyncio.wait_for(started.wait(), 2)
        app.action_detach()
    assert cancelled.is_set()
