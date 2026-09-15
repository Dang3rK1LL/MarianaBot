import asyncio
from pathlib import Path

from textual import events
from textual.widgets import OptionList

from marianabot.chat import Composer, MarianaChat, SessionsScreen
from marianabot.config import Config
from marianabot.store import Store
from marianabot.usage import normalize_usage


class FakeManager:
    def __init__(self):
        self.record = None
        self.starts = []

    def active(self):
        return self.record

    async def start(self, run_id, *, messages_only=False):
        self.starts.append((run_id, messages_only))
        if self.record:
            return False
        self.record = {"run_id": run_id, "mode": "messages" if messages_only else "research"}
        return True


def chat(tmp_path):
    return MarianaChat(
        tmp_path / "state", tmp_path / "mariana.toml", demo=True, manager=FakeManager()
    )


async def send(app, pilot, text):
    app.query_one(Composer).load_text(text)
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


async def test_multiline_paste_send_followup_and_steer(tmp_path):
    app = chat(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        editor = app.query_one(Composer)
        problem = (
            "Launch a business.\n" + "Detailed context. " * 1700 + "\nFinal constraint: no debt."
        )
        app.post_message(events.Paste(problem))
        await pilot.pause()
        assert editor.text == problem
        async with asyncio.timeout(2):
            while editor.region.height != 8:
                await pilot.pause(0.05)
        assert editor.region.height == 8
        assert app.run_id is None  # A pasted newline must never submit the draft.
        await pilot.press("enter")
        await pilot.pause()
        assert app.store.run(app.run_id)["problem"] == problem
        assert app.manager.starts == [(app.run_id, False)]
        assert editor.text == ""
        assert editor.region.height == 3
        await send(app, pilot, "What is the biggest risk?")
        await send(app, pilot, "/steer Limit spending to EUR 500.\nFocus on local buyers.")
        commands = app.store.commands(app.run_id)
        assert [c["kind"] for c in commands] == ["ask", "steer"]
        assert "local buyers" in commands[-1]["text"]
        await send(app, pilot, "/pause")
        assert app.store.run(app.run_id)["control"] == "pause"


async def test_keyboard_suggestions_help_and_small_terminal(tmp_path):
    app = chat(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        assert app.query_one("#conversation").region.height >= 10
        assert app.query_one("#send").region.bottom <= 24
        await pilot.press("a", "ctrl+j", "b")
        assert app.query_one(Composer).text == "a\nb"
        app.query_one(Composer).load_text("/ste")
        await pilot.pause()
        assert app.matches == ["/steer"]
        await pilot.press("tab")
        await pilot.pause()
        assert app.query_one(Composer).text == "/steer "
        app.query_one(Composer).load_text("/")
        await pilot.pause()
        await pilot.press("down", "tab")
        assert app.query_one(Composer).text == "/ask "
        await pilot.press("f1")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "HelpScreen"
        await pilot.pause(1)  # Polling and draft saving must keep working under a modal.
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused == app.query_one(Composer)
        await pilot.resize_terminal(120, 40)
        await pilot.pause()
        assert app.query_one("#conversation").region.width == 120


async def test_drafts_sessions_and_queued_message_survive_reopening(tmp_path):
    app = chat(tmp_path)
    async with app.run_test() as pilot:
        await send(app, pilot, "First problem")
        run_id = app.run_id
        app.query_one(Composer).load_text("Unsent follow-up\nwith details")
        await pilot.pause()
        await pilot.press("ctrl+n")
        await pilot.pause()
        assert app.run_id is None
        app.query_one(Composer).load_text("Another unsent problem")
        await pilot.pause()
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert isinstance(app.screen, SessionsScreen)
        assert app.screen.query_one(OptionList).option_count == 1
        await pilot.press("enter")
        await pilot.pause()
        assert app.run_id == run_id
        assert app.query_one(Composer).text == "Unsent follow-up\nwith details"
        app.action_detach()
    reopened = chat(tmp_path)
    async with reopened.run_test() as pilot:
        assert reopened.run_id == run_id
        assert reopened.query_one(Composer).text == "Unsent follow-up\nwith details"
        await pilot.press("ctrl+n")
        await pilot.pause()
        assert reopened.query_one(Composer).text == "Another unsent problem"


async def test_invalid_action_preserves_draft_and_load_is_editable(tmp_path):
    app = chat(tmp_path)
    path = tmp_path / "long problem.md"
    path.write_text("Loaded business problem\nSecond line", encoding="utf-8")
    async with app.run_test() as pilot:
        await send(app, pilot, "/unknown command")
        assert app.query_one(Composer).text == "/unknown command"
        await send(app, pilot, f'/load "{path}"')
        assert app.query_one(Composer).text == path.read_text(encoding="utf-8")
        assert app.run_id is None
        await pilot.press("enter")
        await pilot.pause()
        await send(app, pilot, "/export")
        assert list(Path(tmp_path / "state" / "exports").rglob("conversation.md"))


async def test_typing_remains_responsive_while_worker_starts(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()

    class SlowManager(FakeManager):
        async def start(self, run_id, *, messages_only=False):
            started.set()
            await release.wait()
            return await super().start(run_id, messages_only=messages_only)

    app = chat(tmp_path)
    app.manager = SlowManager()
    async with app.run_test() as pilot:
        await send(app, pilot, "Start this business research")
        await asyncio.wait_for(started.wait(), 2)
        await pilot.press("h", "e", "l", "l", "o")
        assert app.query_one(Composer).text == "hello"
        release.set()
        await pilot.pause()
        assert app.query_one(Composer).text == "hello"


async def test_real_worker_continues_after_chat_exit(tmp_path):
    from marianabot.store import Store
    from marianabot.worker import WorkerManager

    state = tmp_path / "state"
    app = MarianaChat(state, tmp_path / "config.toml", demo=True)
    async with app.run_test() as pilot:
        await send(app, pilot, "A simple business pilot")
        async with asyncio.timeout(15):
            while app.submitting:
                await pilot.pause(0.1)
        run_id = app.run_id
        app.action_detach()
    manager = WorkerManager(state)
    store = Store(state)
    try:
        async with asyncio.timeout(15):
            while manager.active():
                await asyncio.sleep(0.1)
        assert store.run(run_id)["status"] == "complete"
        assert len(store.rounds(run_id)) == 2
    finally:
        store.close()
    reopened = MarianaChat(state, tmp_path / "config.toml", demo=True)
    async with reopened.run_test() as pilot:
        assert reopened.run_id == run_id
        assert reopened.store.run(run_id)["status"] == "complete"
        await send(reopened, pilot, "What evidence is missing?")
        async with asyncio.timeout(15):
            while not reopened.store.commands(run_id)[0]["answer"]:
                await pilot.pause(0.1)
        assert reopened.store.run(run_id)["status"] == "complete"
        async with asyncio.timeout(15):
            while reopened.manager.active():
                await pilot.pause(0.1)


async def test_slash_quit_and_unsent_demo_mode_persist(tmp_path):
    state = tmp_path / "state"
    app = MarianaChat(state, tmp_path / "config.toml", manager=FakeManager())
    async with app.run_test() as pilot:
        await send(app, pilot, "/demo")
        assert app.demo
        app.query_one(Composer).load_text("My unsent demo problem")
        await pilot.pause()
        app.save_draft(force=True)
    reopened = MarianaChat(state, tmp_path / "config.toml", manager=FakeManager())
    async with reopened.run_test() as pilot:
        assert reopened.demo
        assert reopened.query_one(Composer).text == "My unsent demo problem"
        await send(reopened, pilot, "/quit")
        assert reopened.exiting


async def test_usage_follows_background_work_and_stays_visible_while_reading_and_typing(tmp_path):
    app = chat(tmp_path)
    app.default_demo = app.demo = False
    run_id = app.store.create_run("Track a business research run", Config())
    app.manager.record = {"run_id": run_id, "mode": "research"}
    writer = Store(app.store.directory)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await app.open_run(run_id)
            call_id = writer.begin_call(run_id, "rb", "RB", "openai", "fixture")
            other_id = writer.begin_call(run_id, "jb", "JB", "anthropic", "fixture")
            await pilot.pause(1)
            assert "— in" in str(app.query_one("#usage-openai").content)
            assert "1 active" in str(app.query_one("#usage-openai").content)
            assert "not reported" in str(app.query_one("#limit-anthropic").content)
            writer.record_usage(
                call_id,
                normalize_usage("openai", {"input_tokens": 12000, "output_tokens": 812}),
                final=True,
            )
            writer.record_usage(
                other_id,
                normalize_usage(
                    "anthropic",
                    {"input_tokens": 100, "cache_read_input_tokens": 300, "output_tokens": 50},
                ),
            )
            await pilot.pause(1)
            assert "12,000 in" in str(app.query_one("#usage-openai").content)
            assert "400 in" in str(app.query_one("#usage-anthropic").content)
            assert "partial" in str(app.query_one("#usage-anthropic").content)
            for i in range(10):
                await app.add_card("RB", f"Research {i}", "Plan paragraph.\n\n" * 5)
            app.query_one("#conversation").scroll_home(animate=False)
            app.query_one(Composer).load_text("/")
            await pilot.resize_terminal(80, 24)
            await pilot.pause(1)
            strip = app.query_one("#usage-strip").region
            for selector in (
                "#usage-openai",
                "#usage-anthropic",
                "#limit-openai",
                "#limit-anthropic",
            ):
                widget = app.query_one(selector)
                assert widget.visible
                assert strip.contains_region(widget.region)
                assert widget.region.bottom < app.query_one(Composer).region.y
                assert len(str(widget.content)) <= widget.content_region.width
            assert app.query_one("#conversation").region.height >= 6
            await app.new_conversation(False)
            await pilot.pause(1)
            assert run_id in str(app.query_one("#usage-scope").content)
            assert "12,000 in" in str(app.query_one("#usage-openai").content)
            app.manager.record = None
            await app.open_run(run_id)
            await pilot.pause(1)
            assert "1 active" not in str(app.query_one("#usage-openai").content)
            assert "12,000 in" in str(app.query_one("#usage-openai").content)
    finally:
        writer.close()
