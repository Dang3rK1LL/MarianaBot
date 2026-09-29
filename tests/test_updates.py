import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from filelock import FileLock

from marianabot import bootstrap, updater
from marianabot.runtime import installation_lease


def git(root, *args):
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), *args],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    remote = tmp_path / "remote"
    remote.mkdir()
    git(remote, "init", "--initial-branch=main")
    git(remote, "config", "user.email", "fixture@example.invalid")
    git(remote, "config", "user.name", "Offline fixture")
    (remote / "pyproject.toml").write_text("# offline fixture\n", encoding="utf-8")
    (remote / ".gitignore").write_text(
        ".mariana-updates/\n.mariana/\n.venv/\nmariana.toml\nmariana-server.json\ndiscord.toml\ndiscord-token.txt\n",
        encoding="utf-8",
    )
    (remote / "app.txt").write_text("original", encoding="utf-8")
    git(remote, "add", ".")
    git(remote, "commit", "-m", "Initial fixture")
    root = tmp_path / "installation"
    git(tmp_path, "clone", str(remote), str(root))
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Offline fixture")
    monkeypatch.setattr(updater, "TRUSTED_ORIGINS", {str(remote)})
    (root / "mariana.toml").write_text("[updates]\nenabled=true\n", encoding="utf-8")
    (root / "discord-token.txt").write_text("private-fixture-token", encoding="utf-8")
    (root / ".mariana").mkdir()
    (root / ".mariana" / "research.txt").write_text("private research", encoding="utf-8")
    original = git(root, "rev-parse", "HEAD")
    (remote / "app.txt").write_text("updated", encoding="utf-8")
    git(remote, "commit", "-am", "Updated fixture")
    target = git(remote, "rev-parse", "HEAD")
    return SimpleNamespace(root=root, remote=remote, original=original, target=target)


def prepare(root, target, destination, config, emit):
    # Network-free substitute for the separately exercised virtualenv installer.
    environment = destination / "venv"
    python = updater.python_in(environment)
    python.parent.mkdir(parents=True)
    python.write_text("offline fixture", encoding="utf-8")
    (destination / "ready").write_text(target, encoding="ascii")
    return environment


def update(repo, **kwargs):
    return updater.update(
        repo.root,
        config=repo.root / "mariana.toml",
        data_dir=repo.root / ".mariana",
        emit=lambda _: None,
        **kwargs,
    )


def test_fast_forward_activates_only_after_preparation_and_preserves_private_files(
    repo, monkeypatch
):
    def checked_prepare(*args):
        assert git(repo.root, "rev-parse", "HEAD") == repo.original
        assert updater.read_state(repo.root) is None
        return prepare(*args)

    monkeypatch.setattr(updater, "prepare_environment", checked_prepare)
    assert update(repo).state == "updated"
    assert git(repo.root, "rev-parse", "HEAD") == repo.target
    assert updater.selected_environment(repo.root).is_dir()
    assert (repo.root / "discord-token.txt").read_text() == "private-fixture-token"
    assert (repo.root / ".mariana/research.txt").read_text() == "private research"
    assert git(repo.root, "status", "--porcelain") == ""
    assert update(repo).state == "current"


def test_check_only_does_not_prepare_or_change_installation(repo, monkeypatch):
    monkeypatch.setattr(
        updater, "prepare_environment", lambda *_: pytest.fail("Check must not install")
    )
    assert update(repo, check_only=True).state == "available"
    assert git(repo.root, "rev-parse", "HEAD") == repo.original
    assert updater.read_state(repo.root) is None


@pytest.mark.parametrize("kind", ["tracked", "untracked", "branch", "diverged", "remote"])
def test_local_work_and_untrusted_remotes_are_never_overwritten(repo, monkeypatch, kind):
    monkeypatch.setattr(
        updater, "prepare_environment", lambda *_: pytest.fail("Unsafe checkout must not install")
    )
    if kind == "tracked":
        (repo.root / "app.txt").write_text("my changes")
    elif kind == "untracked":
        (repo.root / "my-notes.txt").write_text("preserve me")
    elif kind == "branch":
        git(repo.root, "checkout", "-b", "development")
    elif kind == "remote":
        git(repo.root, "remote", "set-url", "origin", "https://example.invalid/not-marianabot")
    else:
        (repo.root / "app.txt").write_text("my own commit")
        git(repo.root, "commit", "-am", "Local work")
    before = git(repo.root, "rev-parse", "HEAD")
    contents = (repo.root / "app.txt").read_bytes()
    assert update(repo).state == "skipped"
    assert git(repo.root, "rev-parse", "HEAD") == before
    assert (repo.root / "app.txt").read_bytes() == contents


@pytest.mark.parametrize("held", ["lease", "worker.lock", "launch.lock", "discord.lock", "updater"])
def test_update_waits_for_live_processes_and_other_launches(repo, monkeypatch, held):
    monkeypatch.setattr(
        updater,
        "prepare_environment",
        lambda *_: pytest.fail("Active installation must not change"),
    )
    if held == "lease":
        lock = installation_lease(repo.root)
    elif held == "updater":
        directory = updater.update_directory(repo.root)
        lock = FileLock(str(directory / "update.lock"))
    else:
        lock = FileLock(str(repo.root / ".mariana" / held))
    with lock:
        assert update(repo).state == "deferred"
    assert git(repo.root, "rev-parse", "HEAD") == repo.original


def test_failed_installer_keeps_previous_environment_and_discards_only_candidate(repo, monkeypatch):
    directory = updater.update_directory(repo.root)
    prior = directory / "installs" / "prior"
    prior.mkdir(parents=True)
    environment = prepare(repo.root, repo.original, prior, repo.root / "mariana.toml", print)
    previous = {"commit": repo.original, "environment": str(environment.relative_to(directory))}
    updater.write_state(repo.root, previous)

    def broken(*args):
        prepare(*args)
        raise updater.UpdateFailure("private installer output must not appear")

    monkeypatch.setattr(updater, "prepare_environment", broken)
    result = update(repo)
    assert result.state == "unavailable"
    assert "private installer output" not in result.message
    assert updater.read_state(repo.root) == previous
    assert list((directory / "installs").iterdir()) == [prior]
    assert git(repo.root, "rev-parse", "HEAD") == repo.original


def test_checkout_changed_during_preparation_is_preserved(repo, monkeypatch):
    def changed(*args):
        environment = prepare(*args)
        (repo.root / "app.txt").write_text("edited while downloading")
        return environment

    monkeypatch.setattr(updater, "prepare_environment", changed)
    assert update(repo).state == "deferred"
    assert (repo.root / "app.txt").read_text() == "edited while downloading"
    assert git(repo.root, "rev-parse", "HEAD") == repo.original
    assert updater.read_state(repo.root) is None


@pytest.mark.parametrize("failure", ["fetch", "merge", "state"])
def test_network_and_activation_failure_leave_checkout_usable(repo, monkeypatch, failure):
    original_git = updater.git

    def broken_git(root, *args, **kwargs):
        if args[0] == failure:
            raise updater.UpdateFailure("credential-bearing output")
        return original_git(root, *args, **kwargs)

    monkeypatch.setattr(updater, "git", broken_git)
    monkeypatch.setattr(updater, "prepare_environment", prepare)
    if failure == "state":
        monkeypatch.setattr(
            updater, "write_state", lambda *_: (_ for _ in ()).throw(OSError("disk full"))
        )
    result = update(repo)
    assert result.state == "unavailable"
    assert "credential" not in result.message and "disk full" not in result.message
    assert git(repo.root, "rev-parse", "HEAD") == repo.original
    assert updater.read_state(repo.root) is None


def test_corrupt_or_escaping_activation_pointer_is_ignored(repo):
    directory = updater.update_directory(repo.root)
    for text in (
        "broken json",
        json.dumps({"commit": repo.original, "environment": "../../another-python"}),
    ):
        (directory / "active.json").write_text(text, encoding="utf-8")
        assert updater.read_state(repo.root) is None


def test_startup_preferences_and_offline_commands(repo, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("MARIANA_AUTO_UPDATE", raising=False)
    path = repo.root / "mariana.toml"
    assert updater.automatic_enabled(path)
    path.write_text("[updates]\nenabled=false\n")
    assert not updater.automatic_enabled(path)
    path.write_text("[updates]\nenabled=true\n")
    monkeypatch.setenv("MARIANA_AUTO_UPDATE", "0")
    assert not updater.automatic_enabled(path)
    assert all(
        bootstrap.is_start(args)
        for args in ([], ["chat"], ["run", "id"], ["discord", "run"], ["--connect-server"])
    )
    assert not any(
        bootstrap.is_start(args)
        for args in (
            ["chat", "--demo"],
            ["demo"],
            ["--help"],
            ["doctor"],
            ["status"],
            ["--service", "backup"],
        )
    )


def test_bootstrap_launches_selected_runtime_and_forwards_arguments(repo, monkeypatch):
    destination = updater.update_directory(repo.root) / "installs" / "ready"
    destination.mkdir(parents=True)
    environment = prepare(repo.root, repo.original, destination, repo.root / "mariana.toml", print)
    updater.write_state(
        repo.root,
        {
            "commit": repo.original,
            "environment": str(environment.relative_to(repo.root / ".mariana-updates")),
        },
    )
    monkeypatch.setattr(bootstrap, "installation_root", lambda: repo.root)
    monkeypatch.setattr(
        sys, "argv", ["mariana", "--no-update", "chat", "--data-dir", "custom-data"]
    )
    monkeypatch.setattr(bootstrap, "update", lambda *_a, **_k: pytest.fail("Update was disabled"))

    def launch(args, env):
        assert args == [
            str(updater.python_in(environment)),
            "-m",
            "marianabot",
            "chat",
            "--data-dir",
            "custom-data",
        ]
        assert env["MARIANA_INSTALL_ROOT"] == str(repo.root)
        assert env["MARIANA_SKIP_UPDATE_ONCE"] == "1"
        return 17

    monkeypatch.setattr(bootstrap.subprocess, "call", launch)
    with pytest.raises(SystemExit) as result:
        bootstrap.main()
    assert result.value.code == 17
    assert not list((repo.root / ".mariana-updates/processes").glob("*.lock"))


def test_process_timeout_and_diagnostics_are_bounded(tmp_path):
    with pytest.raises(updater.UpdateFailure, match="timed out"):
        updater.process(
            [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, timeout=0.2
        )
    with pytest.raises(updater.UpdateFailure) as failure:
        updater.process(
            [
                sys.executable,
                "-c",
                "import sys; print('secret-test-value',file=sys.stderr); sys.exit(1)",
            ],
            tmp_path,
        )
    assert "secret-test-value" not in str(failure.value)
