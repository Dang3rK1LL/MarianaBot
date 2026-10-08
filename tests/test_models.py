import pytest
from textual.widgets import Input, Select
from textual.widgets._select import InvalidSelectValueError

from marianabot.chat import Composer, MarianaChat
from marianabot.config import DEFAULT_TOML, Config, load_config, save_model_preferences
from marianabot.model_catalog import ModelOption
from marianabot.models_ui import ModelsScreen

CATALOGS = {
    "rb": [
        ModelOption("gpt-6-luna", "Luna", ("low", "medium", "high")),
        ModelOption("gpt-6-astra", "GPT-6 Astra", ("low", "medium", "high", "xhigh", "max")),
        ModelOption("future-research-model", "Future research", ("medium",), "medium"),
    ],
    "jb": [
        ModelOption("claude-opus-5-5", "Opus 5.5", ("low", "medium", "high", "xhigh", "max")),
        ModelOption("future-judge-model", "Future judge", ("high", "max"), "high"),
        ModelOption("claude-haiku-fixture", "Haiku", (), "auto"),
    ],
}


@pytest.fixture(autouse=True)
def offline_catalogs(monkeypatch):
    async def load(provider, config, cwd):
        return CATALOGS[provider]

    monkeypatch.setattr("marianabot.models_ui.load_models", load)


class FakeManager:
    def active(self):
        return None

    async def start(self, *args, **kwargs):
        return True


def test_preferences_preserve_billing_and_research_settings_and_detect_external_edits(tmp_path):
    path = tmp_path / "mariana.toml"
    source = DEFAULT_TOML.replace("overage_disabled = false", "overage_disabled = true").replace(
        "max_hours = 72", "max_hours = 168"
    )
    path.write_text(source, encoding="utf-8")
    config = load_config(path)
    config.rb.model, config.rb.effort = "future-research-model", "xhigh"
    config.jb.model, config.jb.effort = "future-judge-model", "max"
    save_model_preferences(path, config, source)
    saved = load_config(path)
    assert saved == config
    assert saved.subscription.overage_disabled and saved.research.max_hours == 168
    assert "# in both accounts." in path.read_text(encoding="utf-8")
    new_source = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="changed on disk"):
        save_model_preferences(path, config, source)
    assert path.read_text(encoding="utf-8") == new_source


def test_preferences_support_minimal_and_missing_configuration(tmp_path):
    path = tmp_path / "mariana.toml"
    config = Config()
    config.rb.model = "future-model"
    save_model_preferences(path, config, None)
    assert load_config(path).rb.model == "future-model"
    assert not load_config(path).subscription.overage_disabled
    minimal = "# Existing minimal config\n[subscription]\noverage_disabled = true\n[rb]\nconcurrency = 2\n"
    path.write_text(minimal, encoding="utf-8")
    save_model_preferences(path, config, minimal)
    saved = load_config(path)
    assert saved.subscription.overage_disabled
    assert saved.rb.concurrency == 2
    assert saved.rb.model == "future-model"


async def test_models_screen_saves_new_run_preferences_without_changing_existing_run(tmp_path):
    path = tmp_path / "mariana.toml"
    path.write_text(
        DEFAULT_TOML.replace("overage_disabled = false", "overage_disabled = true"),
        encoding="utf-8",
    )
    app = MarianaChat(tmp_path / "state", path, manager=FakeManager())
    old_run = app.store.create_run("Existing research", load_config(path))
    async with app.run_test(size=(80, 24)) as pilot:
        await app.open_run(old_run)
        app.action_models()
        await pilot.pause()
        assert isinstance(app.screen, ModelsScreen)
        assert app.screen.query_one("#save-models").region.bottom < 24
        assert app.screen.query_one("#rb-model", Select).value == "gpt-6-astra"
        app.screen.query_one("#rb-model", Select).value = "future-research-model"
        await pilot.pause()
        app.screen.query_one("#rb-effort", Select).value = "medium"
        app.screen.query_one("#jb-model", Select).value = "future-judge-model"
        await pilot.pause()
        app.screen.query_one("#jb-effort", Select).value = "max"
        await pilot.click("#save-models")
        await pilot.pause()
        assert app.focused == app.query_one(Composer)
        saved = load_config(path)
        assert saved.rb.model == "future-research-model" and saved.rb.effort == "medium"
        assert saved.jb.model == "future-judge-model" and saved.jb.effort == "max"
        assert (
            Config.model_validate_json(app.store.run(old_run)["config"]).rb.model == "gpt-6-astra"
        )
        await app.new_conversation(False)
        app.query_one(Composer).load_text("New research with saved preferences")
        await pilot.press("enter")
        await pilot.pause()
        app.screen.query_one("#work-folder", Input).value = str(tmp_path / "work")
        await pilot.click("#choose-work-folder")
        await pilot.pause()
        snapshot = Config.model_validate_json(app.store.run(app.run_id)["config"])
        assert snapshot == saved
        app.action_models()
        await pilot.pause()
        await pilot.click("#default-models")
        await pilot.pause()
        assert app.screen.query_one("#rb-model", Select).value == "gpt-6-astra"
        await pilot.press("escape")
        await pilot.pause()
        assert load_config(path) == saved  # Defaults are only persisted after Save.


async def test_unavailable_saved_model_requires_selection_and_cancel_preserves_draft(tmp_path):
    path = tmp_path / "mariana.toml"
    path.write_text(DEFAULT_TOML.replace('model = "gpt-6-astra"', 'model = "unavailable-model"'))
    original = path.read_text()
    app = MarianaChat(tmp_path / "state", path, demo=True, manager=FakeManager())
    async with app.run_test(size=(80, 24)) as pilot:
        app.query_one(Composer).load_text("A long business problem to keep")
        app.action_models()
        await pilot.pause()
        await pilot.click("#save-models")
        await pilot.pause()
        assert isinstance(app.screen, ModelsScreen)
        assert "Choose an available ChatGPT model" in str(
            app.screen.query_one("#model-error").content
        )
        await pilot.press("escape")
        await pilot.pause()
        assert path.read_text() == original
        assert app.query_one(Composer).text == "A long business problem to keep"


async def test_effort_menu_changes_with_model_and_fixed_effort_is_disabled(tmp_path):
    app = MarianaChat(
        tmp_path / "state", tmp_path / "mariana.toml", demo=True, manager=FakeManager()
    )
    async with app.run_test(size=(80, 24)) as pilot:
        app.action_models()
        await pilot.pause()
        screen = app.screen
        assert (
            screen.query_one("#rb-model", Select).region.width
            > screen.query_one("#rb-effort", Select).region.width
        )
        screen.query_one("#rb-model", Select).value = "future-research-model"
        await pilot.pause()
        effort = screen.query_one("#rb-effort", Select)
        assert effort.value == "medium"
        with pytest.raises(InvalidSelectValueError):
            effort.value = "max"
        screen.query_one("#jb-model", Select).value = "claude-haiku-fixture"
        await pilot.pause()
        assert screen.query_one("#jb-effort", Select).disabled
        assert screen.query_one("#jb-effort", Select).value == "auto"
        await pilot.click("#save-models")
        await pilot.pause()
        assert load_config(tmp_path / "mariana.toml").jb.effort == "auto"


async def test_provider_failure_disables_save_and_retry_recovers(tmp_path, monkeypatch):
    from marianabot.clients import ClientError

    async def failing(provider, config, cwd):
        if provider == "jb":
            raise ClientError("fixture failure")
        return CATALOGS[provider]

    monkeypatch.setattr("marianabot.models_ui.load_models", failing)
    app = MarianaChat(
        tmp_path / "state", tmp_path / "mariana.toml", demo=True, manager=FakeManager()
    )
    async with app.run_test(size=(80, 24)) as pilot:
        app.action_models()
        await pilot.pause()
        assert app.screen.query_one("#save-models").disabled
        assert "Claude: Model list unavailable" in str(
            app.screen.query_one("#model-status").content
        )
        assert not app.screen.query_one("#retry-models").disabled

        async def recovered(provider, config, cwd):
            return CATALOGS[provider]

        monkeypatch.setattr("marianabot.models_ui.load_models", recovered)
        await pilot.click("#retry-models")
        await pilot.pause()
        assert not app.screen.query_one("#save-models").disabled


async def test_closing_models_cancels_pending_provider_queries_and_keeps_draft(
    tmp_path, monkeypatch
):
    import asyncio

    started, cancelled = set(), set()

    async def loading(provider, config, cwd):
        started.add(provider)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.add(provider)

    monkeypatch.setattr("marianabot.models_ui.load_models", loading)
    app = MarianaChat(
        tmp_path / "state", tmp_path / "mariana.toml", demo=True, manager=FakeManager()
    )
    async with app.run_test(size=(80, 24)) as pilot:
        app.query_one(Composer).load_text("Keep this research draft")
        app.action_models()
        await pilot.pause()
        assert started == {"rb", "jb"}
        assert app.screen.query_one("#save-models").disabled
        await pilot.press("escape")
        await pilot.pause()
        assert cancelled == started
        assert app.query_one(Composer).text == "Keep this research draft"
