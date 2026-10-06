import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from marianabot.cli import app
from marianabot.config import Config
from marianabot.discord_bridge import DiscordBridge, status_text
from marianabot.discord_config import DiscordConfig, load_discord, read_token, write_private
from marianabot.store import Store


class Manager:
    def __init__(self):
        self.starts = []

    async def start(self, run_id, *, messages_only=False):
        self.starts.append((run_id, messages_only))
        return True


@pytest.fixture
def bridge(store):
    settings = DiscordConfig(
        enabled=True, guild_id=100, channel_id=200, allowed_user_ids=[300], allow_control=True
    )
    return DiscordBridge(store, settings, Manager())


async def command(bridge, action, request_id="1", **kwargs):
    return await bridge.command(
        guild=100, channel=200, user=300, request_id=request_id, action=action, **kwargs
    )


@pytest.mark.parametrize(
    "change", [dict(guild=None), dict(guild=101), dict(channel=201), dict(user=301)]
)
async def test_discord_denies_other_users_destinations_and_dms(bridge, change):
    args = dict(guild=100, channel=200, user=300, request_id="1", action="sessions") | change
    with pytest.raises(PermissionError):
        await bridge.command(**args)
    assert bridge.store.db.execute("SELECT COUNT(*) FROM discord_requests").fetchone()[0] == 0
    assert not bridge.manager.starts


async def test_discord_opt_in_watch_control_and_idempotency(bridge):
    store = bridge.store
    run_id = store.create_run("Private plan", Config(), demo=True)
    assert "Select research" in await command(bridge, "status")
    await command(bridge, "watch", "2", run_id=run_id)
    result = await command(bridge, "ask", "3", message="Explain the risks")
    assert "Saved ask #" in result
    assert await command(bridge, "ask", "3", message="Do not enqueue this duplicate") == result
    assert len(store.commands(run_id)) == 1
    assert bridge.manager.starts == [(run_id, True)]
    await command(bridge, "pause", "4")
    await command(bridge, "steer", "5", message="Require customer interviews")
    assert store.run(run_id)["status"] == "paused"
    assert len(bridge.manager.starts) == 1
    bridge.config.allow_control = False
    with pytest.raises(PermissionError):
        await command(bridge, "resume", "6")
    bridge.config.enabled = False
    with pytest.raises(PermissionError):
        await command(bridge, "status", "7")


async def test_uncertain_request_is_not_replayed_and_closed_run_does_not_restart(bridge):
    run_id = bridge.store.create_run("Closed plan", Config(), demo=True)
    await command(bridge, "watch", run_id=run_id)
    bridge.store.update_run(run_id, status="complete")
    assert "closed" in await command(bridge, "resume", "2")
    with bridge.store.db:
        bridge.store.db.execute(
            "INSERT INTO discord_requests VALUES(?,?,?)",
            (bridge.scope, "3", "Earlier request recorded; outcome uncertain."),
        )
    assert "uncertain" in await command(bridge, "ask", "3", message="Should not run")
    assert not bridge.store.commands(run_id)
    assert not bridge.manager.starts


def review():
    return dict(
        score=70,
        verdict="revise",
        next_prompt="Interview five customers",
        blocking_issues=["No customer evidence"],
        strengths=[],
        human_tests=[],
        dissent=[],
    )


async def test_outbox_survives_failure_restart_and_unwatch(bridge):
    store = bridge.store
    run_id = store.create_run("Watched plan", Config(), demo=True)
    other = store.create_run("Unwatched secret", Config(), demo=True)
    await command(bridge, "watch", run_id=run_id)
    plan = "## Round summary\nResearched pricing.\n## Changes this round\nReduced pilot size.\n## Next direction\nCollect evidence.\n## Full plan\nDetails."
    store.save_round(run_id, 1, 0, plan, review())
    store.save_round(other, 1, 0, "UNWATCHED SECRET", review())
    bridge.collect()
    bridge.collect()
    assert store.db.execute("SELECT COUNT(*) FROM discord_outbox").fetchone()[0] == 1
    broken = AsyncMock(side_effect=OSError("offline"))
    with pytest.raises(OSError):
        await bridge.deliver(broken)
    assert store.db.execute("SELECT delivered FROM discord_outbox").fetchone()[0] == 0
    restarted = DiscordBridge(store, bridge.config, Manager())
    sent = AsyncMock()
    await restarted.deliver(sent)
    text = sent.call_args.args[0]
    assert "Reduced pilot size" in text and "Interview five" in text
    assert "UNWATCHED SECRET" not in text
    assert "Run tokens" in text and "Elapsed:" in text
    assert "compactions:" not in text and "native token window remaining" not in text
    assert len(text) <= 5800
    assert sent.call_args.kwargs["embed"]["title"] == "Round 1 complete"
    await restarted.deliver(sent)
    assert sent.await_count == 1
    store.message(run_id, "reply-fixture", "MB", "Reply", "An answer @everyone")
    restarted.collect()
    await command(restarted, "unwatch", "9")
    await restarted.deliver(sent)
    assert sent.await_count == 1
    assert not restarted.watch()


async def test_policy_change_requires_new_watch(bridge):
    run_id = bridge.store.create_run("Research", Config(), demo=True)
    await command(bridge, "watch", run_id=run_id)
    modified = bridge.config.model_copy(update={"channel_id": 900})
    different = DiscordBridge(bridge.store, modified, Manager())
    assert not different.watch()
    modified = bridge.config.model_copy(update={"allowed_user_ids": [300, 301]})
    assert not DiscordBridge(bridge.store, modified, Manager()).watch()


def test_status_distinguishes_tokens_app_context_and_unreported_allowance(store):
    run_id = store.create_run("Metrics", Config())
    store.begin_call(run_id, "task", "RB", "openai", "gpt-6-astra")
    text = status_text(store, run_id)
    assert "ChatGPT: unknown in / unknown out · partial" in text
    assert "Allowance not reported" in text
    assert "60,000 characters" in text
    assert "native token window remaining: unknown" in text


def test_setup_is_disabled_by_default_and_never_contains_token(tmp_path, monkeypatch):
    monkeypatch.delenv("MARIANA_DISCORD_BOT_TOKEN", raising=False)
    config = tmp_path / "discord.toml"
    args = ["discord", "setup", "--config", str(config), "--data-dir", str(tmp_path / "state")]
    runner = CliRunner()
    result = runner.invoke(app, args, input="100\n200\n300\n\n\n\n")
    assert result.exit_code == 0, result.stdout
    settings = load_discord(config)
    assert not settings.enabled and not settings.allow_control
    assert settings.data_dir == tmp_path / "state"
    assert not settings.token_file.exists()
    assert runner.invoke(app, args).exit_code == 2
    disabled = runner.invoke(app, ["discord", "run", "--config", str(config)])
    assert disabled.exit_code == 2 and "disabled" in disabled.stdout
    write_private(settings.token_file, "synthetic-token-for-offline-testing\n")
    assert read_token(settings) == "synthetic-token-for-offline-testing"
    assert "synthetic-token" not in config.read_text(encoding="utf-8")
    if os.name != "nt":
        settings.token_file.chmod(0o644)
        with pytest.raises(ValueError, match="private"):
            read_token(settings)
    with pytest.raises(FileExistsError):
        write_private(settings.token_file, "replacement")


def test_base_cli_import_does_not_load_discord_sdk():
    result = subprocess.run(
        [sys.executable, "-c", "import marianabot.cli,sys; assert 'discord' not in sys.modules"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


async def test_real_offline_mb_worker_answers_without_resuming_paused_research(tmp_path):
    store = Store(tmp_path)
    settings = DiscordConfig(
        enabled=True, guild_id=100, channel_id=200, allowed_user_ids=[300], allow_control=True
    )
    bridge = DiscordBridge(store, settings)
    run_id = store.create_run("Offline Discord bridge integration", Config(), demo=True)
    store.update_run(run_id, status="paused", control="pause")
    try:
        await command(bridge, "watch", run_id=run_id)
        await command(bridge, "ask", "2", message="Which assumption needs testing?")
        async with asyncio.timeout(20):
            while bridge.manager.active():
                await asyncio.sleep(0.05)
        assert store.commands(run_id)[0]["answer"]
        assert store.run(run_id)["status"] == "paused"
        sent = AsyncMock()
        await bridge.deliver(sent)
        assert any("MB ·" in call.args[0] for call in sent.call_args_list)
    finally:
        if bridge.manager.active():
            store.update_run(run_id, control="pause")
            async with asyncio.timeout(20):
                while bridge.manager.active():
                    await asyncio.sleep(0.05)
        store.close()


async def test_gateway_adapter_acks_first_and_restricts_mentions(bridge):
    discord = pytest.importorskip("discord")
    from marianabot.discord_bot import MarianaDiscord, ResearchCommands

    bot = MarianaDiscord(bridge)
    assert not bot.intents.message_content and not bot.intents.members
    group = ResearchCommands(bridge)
    assert {c.name for c in group.commands} == {
        "sessions",
        "watch",
        "unwatch",
        "status",
        "ask",
        "steer",
        "pause",
        "resume",
        "retry",
    }
    denied = SimpleNamespace(
        guild_id=100,
        channel_id=200,
        user=SimpleNamespace(id=301),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
    )
    await group.dispatch(denied, "sessions")
    denied.response.send_message.assert_awaited_once()
    denied.response.defer.assert_not_awaited()
    interaction = SimpleNamespace(
        guild_id=100,
        channel_id=200,
        user=SimpleNamespace(id=300),
        id=5,
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=AsyncMock(),
    )

    async def check_ack(**kwargs):
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        return "*" * 3000 + "@everyone"

    bridge.command = check_ack
    await group.dispatch(interaction, "status")
    reply = interaction.edit_original_response.call_args.kwargs
    assert len(reply["content"]) <= 1950
    assert reply["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
    bot.destination = SimpleNamespace(send=AsyncMock())
    await bot.send_update("@everyone **untrusted text**")
    message = bot.destination.send.call_args
    assert "@everyone" not in message.args[0]
    assert message.kwargs["suppress_embeds"]
    await bot.close()


async def test_round_starts_are_ordered_with_recaps_and_survive_restart(bridge):
    from marianabot.engine import Engine

    store = bridge.store
    config = Config()
    config.research.max_rounds = 2
    config.research.min_rounds = 1
    run_id = store.create_run("Round notification test", config, demo=True)
    await command(bridge, "watch", run_id=run_id)
    await Engine(store, run_id).run()
    bridge.collect()
    bodies = [row[0] for row in store.db.execute("SELECT body FROM discord_outbox ORDER BY id")]
    round_messages = [
        body
        for body in bodies
        if body.splitlines()[0]
        in {"Round 1/2 started", "Round 1 complete", "Round 2/2 started", "Round 2 complete"}
    ]
    assert len(round_messages) == 4
    assert round_messages[0].startswith("Round 1/2 started")
    assert round_messages[1].startswith("Round 1 complete")
    assert round_messages[2].startswith("Round 2/2 started")
    assert round_messages[3].startswith("Round 2 complete")
    assert all(len(body) < 5800 for body in round_messages)
    assert all("Offline demo" in body for body in round_messages)
    assert "Round:" in round_messages[1]
    titles = [body.splitlines()[0] for body in bodies]
    assert titles[:7] == [
        "Preparing the research brief",
        "Research brief ready",
        "Round 1/2 started",
        "Round 1 · Plan synthesis",
        "Round 1 · Independent critique",
        "Round 1 · Review decision",
        "Round 1 complete",
    ]
    before = len(bodies)
    restarted = DiscordBridge(store, bridge.config, Manager())
    restarted.collect()
    assert store.db.execute("SELECT COUNT(*) FROM discord_outbox").fetchone()[0] == before
    assert (store.export_directory(run_id) / "history.json").is_file()


async def test_recap_uses_usage_at_completion_and_does_not_reannounce_cached_round(bridge):
    store = bridge.store
    run_id = store.create_run("Usage snapshot", Config(), demo=True)
    await command(bridge, "watch", run_id=run_id)
    for _ in range(2):
        store.event(
            run_id,
            "Started",
            kind="round_started",
            payload={"number": 1, "revision": 0, "max_rounds": 24, "focus": "Test assumptions"},
        )
    store.save_round(run_id, 1, 0, "## Round summary\nTested pricing.", review())
    store.begin_call(run_id, "round-2-revision-0-rb-0", "RB", "openai", "fixture")
    bridge.collect()
    bodies = [row[0] for row in store.db.execute("SELECT body FROM discord_outbox ORDER BY id")]
    assert len(bodies) == 2
    assert "ChatGPT: 0 in / 0 out" in bodies[1]
    assert "partial" not in bodies[1]


def test_hidden_token_command_never_echoes_or_overwrites(tmp_path, monkeypatch):
    monkeypatch.delenv("MARIANA_DISCORD_BOT_TOKEN", raising=False)
    config = tmp_path / "discord.toml"
    settings = DiscordConfig(guild_id=100, channel_id=200, allowed_user_ids=[300])
    config.write_text(
        "guild_id = 100\nchannel_id = 200\nallowed_user_ids = [300]\n", encoding="utf-8"
    )
    result = CliRunner().invoke(
        app, ["discord", "token", "--config", str(config)], input="fixture-private-token\n"
    )
    assert result.exit_code == 0
    assert "fixture-private-token" not in result.stdout
    assert (tmp_path / settings.token_file).read_text().strip() == "fixture-private-token"
    assert "fixture-private-token" not in config.read_text()
    repeated = CliRunner().invoke(
        app, ["discord", "token", "--config", str(config)], input="replacement\n"
    )
    assert repeated.exit_code == 2
    assert (tmp_path / settings.token_file).read_text().strip() == "fixture-private-token"
    replaced = CliRunner().invoke(
        app,
        ["discord", "token", "--config", str(config), "--replace"],
        input="fixture-updated-token\n",
    )
    assert replaced.exit_code == 0
    assert "fixture-updated-token" not in replaced.stdout
    assert (tmp_path / settings.token_file).read_text().strip() == "fixture-updated-token"
    assert not list(tmp_path.glob(".discord-token-*.token"))


@pytest.mark.parametrize("value", ["\x1b[1", "fixture-private-token\x1b[1", "short"])
def test_token_command_rejects_failed_paste_without_replacing_token(tmp_path, monkeypatch, value):
    config = tmp_path / "discord.toml"
    config.write_text("guild_id = 100\nchannel_id = 200\nallowed_user_ids = [300]\n")
    settings = load_discord(config)
    write_private(settings.token_file, "fixture-existing-token\n")
    monkeypatch.setattr("marianabot.discord_cli.typer.prompt", lambda *args, **kwargs: value)
    result = CliRunner().invoke(app, ["discord", "token", "--config", str(config), "--replace"])
    assert result.exit_code == 2
    assert "complete token was not received" in result.stdout
    assert settings.token_file.read_text().strip() == "fixture-existing-token"


@pytest.mark.parametrize("value", ["\x1b[1", "fixture-token\x7f", "fixture\ntoken"])
def test_token_reader_rejects_console_control_characters(tmp_path, monkeypatch, value):
    monkeypatch.delenv("MARIANA_DISCORD_BOT_TOKEN", raising=False)
    settings = DiscordConfig(
        guild_id=100,
        channel_id=200,
        allowed_user_ids=[300],
        token_file=tmp_path / "discord-token.txt",
    )
    write_private(settings.token_file, value)
    with pytest.raises(ValueError, match="invalid characters"):
        read_token(settings)
