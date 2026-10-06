"""Exercise a real staged update in a disposable checkout. Downloads dependencies; no model calls."""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from marianabot import updater
from marianabot.config import DEFAULT_TOML
from marianabot.updater import git


def main():
    project = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="mariana-update-check-") as temporary:
        base = Path(temporary)
        remote, root = base / "remote", base / "installation"
        remote.mkdir()
        for name in ("pyproject.toml", "README.md"):
            shutil.copy2(project / name, remote / name)
        shutil.copytree(
            project / "src",
            remote / "src",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"),
        )
        (remote / ".gitignore").write_text(
            ".mariana-updates/\n.mariana/\nmariana.toml\ndiscord-token.txt\n", encoding="utf-8"
        )
        git(remote, "init", "--initial-branch=main")
        git(remote, "config", "user.name", "Offline updater check")
        git(remote, "config", "user.email", "fixture@example.invalid")
        git(remote, "add", ".")
        git(remote, "commit", "-m", "Before update")
        git(base, "clone", str(remote), str(root))
        (remote / "update-fixture.txt").write_text(
            "Synthetic update; no private data.", encoding="utf-8"
        )
        git(remote, "add", "update-fixture.txt")
        git(remote, "commit", "-m", "Available update")
        (root / "mariana.toml").write_text(DEFAULT_TOML, encoding="utf-8")
        (root / "discord-token.txt").write_text("synthetic-private-file", encoding="utf-8")
        updater.TRUSTED_ORIGINS = {str(remote)}  # This disposable local fixture only.
        result = updater.update(
            root,
            config=root / "mariana.toml",
            data_dir=root / ".mariana",
            emit=lambda message: print(message, flush=True),
        )
        print(result.message, flush=True)
        assert result.state == "updated", result
        environment = updater.selected_environment(root)
        assert environment is not None
        python = updater.python_in(environment)
        assert (root / "discord-token.txt").read_text() == "synthetic-private-file"
        assert (root / "mariana.toml").read_text() == DEFAULT_TOML
        assert not git(root, "status", "--porcelain")
        env = dict(os.environ, MARIANA_INSTALL_ROOT=str(root), MARIANA_AUTO_UPDATE="0")
        process = subprocess.run(
            [
                str(python),
                "-m",
                "marianabot",
                "demo",
                "--plain",
                "--data-dir",
                str(root / ".mariana/demo"),
            ],
            cwd=root,
            env=env,
            capture_output=True,
        )
        assert process.returncode == 0, process.stderr.decode(errors="replace")
        has_discord = importlib.util.find_spec("discord") is not None
        updater.process(
            [
                str(python),
                "-c",
                f"import importlib.util; assert (importlib.util.find_spec('discord') is not None) == {has_discord!r}",
            ],
            root,
            environment=env,
        )
        # Original launcher selects the installed candidate too, without a second update check.
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "marianabot",
                "--no-update",
                "status",
                "--data-dir",
                str(root / ".mariana/demo"),
            ],
            env=env,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        print(
            "PASS: real package install, isolated runtime, fast-forward, launcher handoff, preserved private files, optional Discord, and offline research.",
            flush=True,
        )


if __name__ == "__main__":
    main()
