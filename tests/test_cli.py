from typer.testing import CliRunner

from marianabot.cli import app
from marianabot.config import Config
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
