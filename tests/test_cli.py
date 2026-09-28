from typer.testing import CliRunner

from marianabot.cli import app
from marianabot.config import DEFAULT_TOML, Config, load_config
from marianabot.store import Store

runner = CliRunner()


def test_init_never_overwrites_configuration(tmp_path):
    cfg = tmp_path / "mariana.toml"
    args = ["init", "--config", str(cfg), "--data-dir", str(tmp_path / "state")]
    assert runner.invoke(app, args).exit_code == 0
    cfg.write_text("custom data", encoding="utf-8")
    assert runner.invoke(app, args).exit_code == 2
    assert cfg.read_text(encoding="utf-8") == "custom data"


def test_unknown_run_is_a_clear_error(tmp_path):
    result = runner.invoke(app, ["status", "absent", "--data-dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "Unknown run" in result.stdout
    assert "Traceback" not in result.stdout


def test_setup_defaults_lock_live_use_and_support_future_models(tmp_path):
    cfg = tmp_path / "mariana.toml"
    args = ["setup", "--config", str(cfg), "--data-dir", str(tmp_path / "state")]
    result = runner.invoke(app, args, input="\n\n\n\n\n")
    assert result.exit_code == 0, result.stdout
    saved = load_config(cfg)
    assert saved == Config()
    assert not saved.subscription.overage_disabled
    cfg.write_text(DEFAULT_TOML.replace("max_hours = 72", "max_hours = 168"), encoding="utf-8")
    result = runner.invoke(app, args, input="future-rb\nxhigh\nfuture-jb\nmax\ny\n")
    assert result.exit_code == 0, result.stdout
    saved = load_config(cfg)
    assert saved.rb.model == "future-rb" and saved.rb.effort == "xhigh"
    assert saved.jb.model == "future-jb" and saved.jb.effort == "max"
    assert saved.subscription.overage_disabled
    assert saved.research.max_hours == 168


def test_setup_invalid_choice_is_retried_and_abort_does_not_write(tmp_path):
    cfg = tmp_path / "mariana.toml"
    args = ["setup", "--config", str(cfg), "--data-dir", str(tmp_path / "state")]
    result = runner.invoke(app, args, input="bad;model\nhigh\n")
    assert result.exit_code != 0
    assert not cfg.exists()


def test_stop_cannot_be_resumed(tmp_path):
    store = Store(tmp_path)
    run_id = store.create_run("Stop permanently", Config(), demo=True)
    store.close()
    assert runner.invoke(app, ["stop", run_id, "--data-dir", str(tmp_path)]).exit_code == 0
    result = runner.invoke(app, ["resume", run_id, "--data-dir", str(tmp_path), "--plain"])
    assert result.exit_code == 0
    store = Store(tmp_path)
    assert store.run(run_id)["status"] == "stopped"
    assert not store.calls(run_id)
    store.close()
