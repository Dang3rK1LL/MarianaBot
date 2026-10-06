import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from marianabot.config import Config
from marianabot.discord_bridge import DiscordBridge
from marianabot.discord_config import DiscordConfig
from marianabot.discord_messages import (
    card_text,
    recap_card,
    round_start_card,
    stage_card,
    wait_card,
)


def make_run(config=None):
    return dict(
        id="fixture-run",
        problem="Investigate demand for a small paid pilot",
        config=(config or Config()).model_dump_json(),
        demo=False,
    )


def make_review(**changes):
    return (
        dict(
            score=78,
            verdict="revise",
            next_prompt="Test willingness to pay before expanding",
            blocking_issues=["No paid customer commitments"],
            strengths=["Pilot costs now have an explicit ceiling"],
            human_tests=["Ask three buyers to commit to the pilot"],
            dissent=["An interview is weaker evidence than a paid commitment"],
        )
        | changes
    )


def test_recap_reports_actual_changes_review_delta_and_owner_tests():
    plan = "## Round summary\nCompared two buyer segments.\n## Changes this round\nReduced the pilot from 20 customers to five.\n## Next direction\nCollect paid commitments.\n## Plan\nPrivate extended detail."
    embed = recap_card(
        make_run(),
        dict(number=2, plan=plan, review=make_review()),
        dict(elapsed=120, duration=50),
        make_review(score=71),
    )
    text = card_text(embed)
    assert "+7 since previous round" in text
    assert "Reduced the pilot from 20 customers to five" in text
    assert "No paid customer commitments" in text
    assert "Needs your input" in text and "Ask three buyers" in text
    assert "Disagreements retained" in text
    assert "Private extended detail" not in text


async def test_rich_adapter_enforces_limits_and_keeps_untrusted_formatting_literal(store):
    discord = pytest.importorskip("discord")
    from marianabot.discord_bot import MarianaDiscord, render_embed

    review = make_review(
        next_prompt="*" * 12000 + "@everyone",
        blocking_issues=["`" * 6000] * 30,
        human_tests=["_" * 8000] * 30,
    )
    embed = recap_card(
        make_run(),
        dict(
            number=1,
            plan="## Round summary\n" + "[**@everyone**](https://example.com) " * 500,
            review=review,
        ),
        {},
    )
    rendered = render_embed(embed)
    assert len(rendered) <= 6000
    assert len(rendered.title) <= 256
    assert all(len(field.value) <= 1024 for field in rendered.fields)
    assert all("@everyone" not in field.value for field in rendered.fields)
    assert not rendered.url and not rendered.image.url and not rendered.thumbnail.url
    settings = DiscordConfig(enabled=True, guild_id=100, channel_id=200, allowed_user_ids=[300])
    bot = MarianaDiscord(DiscordBridge(store, settings))
    bot.destination = SimpleNamespace(send=AsyncMock())
    try:
        await bot.send_update(card_text(embed), embed=embed)
        kwargs = bot.destination.send.call_args.kwargs
        assert isinstance(kwargs["embed"], discord.Embed)
        assert not kwargs.get("suppress_embeds")
        assert kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
        bot.embed_enabled = False
        await bot.send_update(card_text(embed), embed=embed)
        call = bot.destination.send.call_args
        assert len(call.args[0]) <= 1950
        assert "**Round 1 complete**" in call.args[0]
        assert call.kwargs["suppress_embeds"]
        assert "embed" not in call.kwargs
    finally:
        await bot.close()


def test_round_token_snapshot_filters_team_calls_and_survives_later_usage(store):
    run_id = store.create_run("Snapshot test", Config(), demo=True)
    for task, count in (
        ("mb-intake", 200),
        ("round-1-revision-0-rb-0", 501),
        ("round-10-revision-0-rb-0", 999),
    ):
        call_id = store.begin_call(run_id, task, "RB", "openai", "fixture")
        store.finish(
            call_id, "done", dict(text="saved", usage=dict(input_tokens=count, output_tokens=10))
        )
    store.save_round(run_id, 1, 0, "## Round summary\nSaved plan", make_review())
    payload = json.loads(
        store.db.execute("SELECT payload FROM events WHERE kind='round_complete'").fetchone()[0]
    )
    store.begin_call(run_id, "round-2-revision-0-rb-0", "RB", "openai", "fixture")
    assert payload["round_usage"]["openai"]["input_tokens"] == 501
    embed = recap_card(
        store.run(run_id), dict(number=1, plan="Saved plan", review=make_review()), payload
    )
    tokens = next(field["value"] for field in embed["fields"] if field["name"] == "ChatGPT tokens")
    assert "Round team: 501 in / 10 out" in tokens
    assert "1,700 in / 30 out" in tokens
    assert "partial" not in tokens


def test_research_stage_cards_describe_actual_team_sizes_and_retrieval_setting():
    config = Config()
    config.rb.agents = 4
    config.jb.agents = 2
    config.research.web_search = False
    run = make_run(config)
    start = round_start_card(
        run,
        dict(
            number=1,
            max_rounds=24,
            focus="Validate pricing",
            roles=["Market research", "Economics"],
        ),
    )
    assert "4 independent proposals" in card_text(start)
    assert "2 independent critiques" in card_text(start)
    assert "Web search disabled" in card_text(start)
    critique = stage_card(
        run,
        dict(
            stage="critique",
            number=1,
            agents=2,
            plan="## Round summary\nDemand remains uncertain.\n## Changes this round\nReduced fixed costs.",
            roles=["Failure investigator"],
        ),
    )
    assert "Demand remains uncertain" in card_text(critique)
    assert "Reduced fixed costs" in card_text(critique)
    assert "Failure investigator" in card_text(critique)
    assert "Wait" in card_text(
        wait_card(run, dict(reason="Claude subscription cooling down", seconds=1800))
    )


async def test_outbox_migration_preserves_old_plain_notifications(store):
    settings = DiscordConfig(enabled=True, guild_id=100, channel_id=200, allowed_user_ids=[300])
    store.db.execute(
        "CREATE TABLE discord_outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT,event_key TEXT,body TEXT,delivered INTEGER DEFAULT 0,UNIQUE(scope,event_key))"
    )
    store.db.execute(
        "INSERT INTO discord_outbox(scope,event_key,body) VALUES(?,?,?)",
        (settings.scope, "legacy", "Pending old notification"),
    )
    store.db.commit()
    bridge = DiscordBridge(store, settings)
    sent = AsyncMock()
    await bridge.deliver(sent)
    sent.assert_awaited_once_with("Pending old notification")


async def test_stage_resume_deduplication_and_batched_message_order(store):
    settings = DiscordConfig(enabled=True, guild_id=100, channel_id=200, allowed_user_ids=[300])
    bridge = DiscordBridge(store, settings)
    run_id = store.create_run("Burst events", Config(), demo=True)
    await bridge.command(
        guild=100, channel=200, user=300, request_id="watch", action="watch", run_id=run_id
    )
    for number in range(60):
        payload = dict(stage="synthesis", number=number, revision=0, agents=3)
        store.event(run_id, "Synthesis", kind="research_stage", payload=payload)
        store.event(run_id, "Cached replay", kind="research_stage", payload=payload)
        store.message(run_id, f"reply-{number}", "MB", f"Reply {number}", "Saved answer")
    for _ in range(3):
        bridge.collect()
    rows = store.db.execute("SELECT body,embed FROM discord_outbox ORDER BY id").fetchall()
    assert len(rows) == 120
    titles = [json.loads(row["embed"])["title"] for row in rows]
    assert titles[:4] == [
        "Round 0 · Plan synthesis",
        "MB · Reply 0",
        "Round 1 · Plan synthesis",
        "MB · Reply 1",
    ]
    restarted = DiscordBridge(store, settings)
    restarted.collect()
    assert store.db.execute("SELECT COUNT(*) FROM discord_outbox").fetchone()[0] == 120
