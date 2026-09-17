import pytest
from textual.widgets import Input, Select

from marianabot.chat import Composer, MarianaChat
from marianabot.config import DEFAULT_TOML, Config, load_config, save_model_preferences
from marianabot.models_ui import ModelsScreen


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
        assert app.screen.query_one("#rb-model", Input).value == "gpt-6-astra"
        app.screen.query_one("#rb-model", Input).value = "future-research-model"
        app.screen.query_one("#rb-effort", Select).value = "medium"
        app.screen.query_one("#jb-model", Input).value = "future-judge-model"
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
        await app.start_problem("New research with saved preferences")
        snapshot = Config.model_validate_json(app.store.run(app.run_id)["config"])
        assert snapshot == saved
        app.action_models()
        await pilot.pause()
        await pilot.click("#default-models")
        await pilot.pause()
        assert app.screen.query_one("#rb-model", Input).value == "gpt-6-astra"
        await pilot.press("escape")
        await pilot.pause()
        assert load_config(path) == saved  # Defaults are only persisted after Save.


async def test_invalid_model_stays_editable_and_cancel_preserves_draft(tmp_path):
    path = tmp_path / "mariana.toml"
    app = MarianaChat(tmp_path / "state", path, demo=True, manager=FakeManager())
    async with app.run_test(size=(80, 24)) as pilot:
        app.query_one(Composer).load_text("A long business problem to keep")
        app.action_models()
        await pilot.pause()
        app.screen.query_one("#rb-model", Input).value = "invalid model; shell"
        await pilot.click("#save-models")
        await pilot.pause()
        assert isinstance(app.screen, ModelsScreen)
        assert "Enter a model ID" in str(app.screen.query_one("#model-error").content)
        await pilot.press("escape")
        await pilot.pause()
        assert not path.exists()
        assert app.query_one(Composer).text == "A long business problem to keep"
