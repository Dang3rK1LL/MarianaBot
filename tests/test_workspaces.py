import asyncio
from pathlib import Path

from textual.widgets import Input
from typer.testing import CliRunner

from marianabot.chat import Composer, MarianaChat
from marianabot.cli import app
from marianabot.config import DEFAULT_TOML, Config
from marianabot.store import Store
from marianabot.workspaces_ui import WorkFolderScreen


def test_separate_run_folders_preserve_existing_files_and_reopen(tmp_path):
    parent = tmp_path / "selected folder"
    parent.mkdir()
    existing = parent / "existing.md"
    existing.write_text("keep me", encoding="utf-8")
    store = Store(tmp_path / "state")
    first = store.create_run("First problem", Config(), work_folder=parent)
    second = store.create_run("Second problem", Config(), work_folder=parent)
    first_folder = Path(store.run(first)["work_dir"])
    second_folder = Path(store.run(second)["work_dir"])
    assert first_folder != second_folder
    assert first_folder.parent == second_folder.parent == parent
    assert (first_folder / "problem.md").read_text().strip() == "First problem"
    assert existing.read_text() == "keep me"
    store.close()
    reopened = Store(tmp_path / "state")
    assert reopened.client_directory(first).parent == first_folder
    assert reopened.export_directory(second).parent == second_folder
    reopened.close()


def test_new_cli_asks_for_folder_before_problem_and_supports_explicit_path(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(DEFAULT_TOML, encoding="utf-8")
    args = ["new", "--config", str(config), "--data-dir", str(tmp_path / "state")]
    result = CliRunner().invoke(
        app, args, input=str(tmp_path / "chosen") + "\nA business problem\n"
    )
    assert result.exit_code == 0, result.stdout
    assert result.stdout.index("Work folder") < result.stdout.index("What business problem")
    explicit = CliRunner().invoke(
        app, args + ["Another problem", "--work-folder", str(tmp_path / "chosen")]
    )
    assert explicit.exit_code == 0, explicit.stdout
    store = Store(tmp_path / "state")
    assert len({run["work_dir"] for run in store.runs()}) == 2
    store.close()


async def test_live_chat_requires_folder_and_cancellation_keeps_draft(tmp_path):
    class Manager:
        def __init__(self):
            self.starts = []

        def active(self):
            return None

        async def start(self, run_id, **kwargs):
            self.starts.append(run_id)

    config = tmp_path / "config.toml"
    config.write_text(
        DEFAULT_TOML.replace("overage_disabled = false", "overage_disabled = true"),
        encoding="utf-8",
    )
    manager = Manager()
    chat = MarianaChat(tmp_path / "state", config, manager=manager)
    async with chat.run_test(size=(80, 24)) as pilot:
        chat.query_one(Composer).load_text("Keep my research problem")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(chat.screen, WorkFolderScreen)
        assert not chat.store.runs() and not manager.starts
        await pilot.press("escape")
        async with asyncio.timeout(5):
            while chat.submitting:
                await pilot.pause(0.05)
        assert chat.query_one(Composer).text == "Keep my research problem"
        assert not chat.store.runs()
        await pilot.press("enter")
        await pilot.pause()
        chat.screen.query_one(Input).value = str(tmp_path / "chosen")
        await pilot.click("#choose-work-folder")
        async with asyncio.timeout(5):
            while chat.submitting:
                await pilot.pause(0.05)
        run_id = chat.run_id
        assert manager.starts == [run_id]
        assert Path(chat.store.run(run_id)["work_dir"]).parent == tmp_path / "chosen"
        await chat.new_conversation(False)
        chat.query_one(Composer).load_text("Second research problem")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(chat.screen, WorkFolderScreen)
        assert len(chat.store.runs()) == 1
        await pilot.press("escape")
