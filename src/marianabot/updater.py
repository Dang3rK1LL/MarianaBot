"""Fetch, prepare and validate updates before changing the installed application."""

import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib
import uuid
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock, Timeout

from marianabot.runtime import installation_root, update_directory

TRUSTED_ORIGINS = {
    "https://github.com/Dang3rK1LL/MarianaBot",
    "https://github.com/Dang3rK1LL/MarianaBot.git",
    "git@github.com:Dang3rK1LL/MarianaBot.git",
    "ssh://git@github.com/Dang3rK1LL/MarianaBot.git",
}


@dataclass
class UpdateResult:
    state: str
    message: str


class UpdateFailure(Exception):
    pass


def process(args: list[str], cwd: Path, *, timeout=20, environment=None) -> str:
    env = dict(os.environ) if environment is None else environment.copy()
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", PIP_NO_INPUT="1")
    # An SSH remote must never stop startup at an authentication prompt.
    env["GIT_SSH_COMMAND"] = (
        "ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes"
    )
    try:
        child = subprocess.Popen(
            args,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            output, _ = child.communicate(timeout=timeout)
        except BaseException:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            child.communicate()
            raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateFailure("Command unavailable or timed out") from exc
    if child.returncode:
        # Raw installer/Git output can include private proxy URLs or credentials.
        raise UpdateFailure(f"Command exited with status {child.returncode}")
    return output.decode("utf-8", errors="replace").strip()


def git(root: Path, *args: str, timeout=20) -> str:
    return process(
        [
            "git",
            "-c",
            f"safe.directory={root.as_posix()}",
            "-c",
            "core.hooksPath=",
            "-C",
            str(root),
            *args,
        ],
        root,
        timeout=timeout,
    )


def read_state(root: Path) -> dict | None:
    try:
        data = json.loads((root / ".mariana-updates" / "active.json").read_text(encoding="utf-8"))
        if not re.fullmatch(r"[0-9a-f]{40,64}", data["commit"]):
            return None
        relative = Path(data["environment"])
        if relative.is_absolute() or ".." in relative.parts:
            return None
        environment = (root / ".mariana-updates" / relative).resolve()
        if not environment.is_relative_to((root / ".mariana-updates" / "installs").resolve()):
            return None
        if not python_in(environment).is_file() or not (environment.parent / "ready").is_file():
            return None
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_state(root: Path, data: dict):
    path = update_directory(root) / "active.json"
    temporary = path.with_name("active-" + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(data) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def python_in(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def selected_environment(root: Path) -> Path | None:
    state = read_state(root)
    if state is None:
        return None
    # An explicit manual checkout/pull is authoritative over an older cached runtime.
    try:
        if git(root, "rev-parse", "HEAD") != state["commit"]:
            return None
    except UpdateFailure:
        return None
    return root / ".mariana-updates" / state["environment"]


def automatic_enabled(config: Path) -> bool:
    if os.environ.get("MARIANA_AUTO_UPDATE", "").lower() in {"0", "false", "off"}:
        return False
    if os.environ.get("CI"):
        return False
    if not config.exists():
        return True
    try:
        return (
            tomllib.loads(config.read_text(encoding="utf-8"))
            .get("updates", {})
            .get("enabled", True)
            is True
        )
    except (OSError, ValueError, AttributeError):
        return False


def prepare_environment(root: Path, target: str, destination: Path, config: Path, emit) -> Path:
    source = destination / "source"
    source.mkdir()
    archive = destination / "source.zip"
    git(root, "archive", "--format=zip", f"--output={archive}", target)
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            if not (source / item.filename).resolve().is_relative_to(source.resolve()):
                raise UpdateFailure("Unsafe path in update archive")
        bundle.extractall(source)
    emit("Preparing the update in a separate environment.")
    environment = destination / "venv"
    process([sys.executable, "-m", "venv", str(environment)], root, timeout=90)
    python = python_in(environment)
    extra = "[discord]" if importlib.util.find_spec("discord") is not None else ""
    clean = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "MARIANA_DISCORD_BOT_TOKEN", "DISCORD_BOT_TOKEN"}
    }
    clean.update(MARIANA_AUTO_UPDATE="0", MARIANA_INSTALL_ROOT=str(root))
    process(
        [
            str(python),
            "-m",
            "pip",
            "--disable-pip-version-check",
            "install",
            "--no-input",
            "--retries",
            "1",
            "--timeout",
            "15",
            str(source) + extra,
        ],
        root,
        timeout=180,
        environment=clean,
    )
    emit("Checking dependencies and application startup.")
    process([str(python), "-m", "pip", "check"], root, timeout=30, environment=clean)
    process(
        [
            str(python),
            "-c",
            "from marianabot.cli import app; from marianabot.worker import WorkerManager; from importlib.resources import files; assert files('marianabot').joinpath('chat.tcss').is_file()",
        ],
        root,
        timeout=30,
        environment=clean,
    )
    if config.exists():
        process(
            [
                str(python),
                "-c",
                "from pathlib import Path; import sys; from marianabot.config import load_config; load_config(Path(sys.argv[1]))",
                str(config.resolve()),
            ],
            root,
            timeout=20,
            environment=clean,
        )
    if extra:
        process(
            [str(python), "-c", "from marianabot.discord_bot import MarianaDiscord"],
            root,
            timeout=20,
            environment=clean,
        )
    (destination / "ready").write_text(target, encoding="ascii")
    return environment


def update(
    root: Path, *, config: Path, data_dir: Path, check_only=False, emit=print
) -> UpdateResult:
    root = root.resolve()
    directory = update_directory(root)
    candidate = None
    activated = False
    step = "Checking GitHub"
    try:
        with ExitStack() as locks:
            locks.enter_context(FileLock(str(directory / "check.lock"), timeout=0))
            if git(root, "remote", "get-url", "origin") not in TRUSTED_ORIGINS:
                return UpdateResult(
                    "skipped",
                    "Automatic updates require the official MarianaBot origin. Keeping this checkout.",
                )
            if git(root, "symbolic-ref", "--short", "HEAD") != "main":
                return UpdateResult(
                    "skipped", "Automatic updates are paused outside the main branch."
                )
            if git(root, "status", "--porcelain", "--untracked-files=normal"):
                return UpdateResult(
                    "skipped",
                    "Local changes found. Keeping the installed version; nothing was overwritten.",
                )
            old_head = git(root, "rev-parse", "HEAD")
            emit("Checking GitHub for MarianaBot updates.")
            git(root, "fetch", "--no-tags", "--no-recurse-submodules", "origin", "main", timeout=20)
            target = git(root, "rev-parse", "FETCH_HEAD^{commit}")
            if target == old_head:
                return UpdateResult("current", "MarianaBot is up to date.")
            try:
                git(root, "merge-base", "--is-ancestor", old_head, target)
            except UpdateFailure:
                return UpdateResult(
                    "skipped",
                    "Local history differs from GitHub. Automatic updates will not reset or merge it.",
                )
            if check_only:
                return UpdateResult(
                    "available", f"Update available: {old_head[:8]} -> {target[:8]}."
                )
            locks.enter_context(FileLock(str(directory / "update.lock"), timeout=0))
            # Hold each lease/worker lock through activation, so nothing can begin mid-update.
            for path in (directory / "processes").glob("*.lock"):
                locks.enter_context(FileLock(str(path), timeout=0))
            for data in {data_dir.resolve(), (root / ".mariana").resolve()}:
                for name in ("launch.lock", "worker.lock", "discord.lock"):
                    path = data / name
                    if path.exists():
                        locks.enter_context(FileLock(str(path), timeout=0))
            step = "Preparing the update"
            installs = directory / "installs"
            installs.mkdir(exist_ok=True, mode=0o700)
            candidate = Path(tempfile.mkdtemp(prefix=target[:12] + "-", dir=installs))
            environment = prepare_environment(root, target, candidate, config, emit)
            # Recheck after the download/build: editors and external Git commands aren't locked.
            if git(root, "rev-parse", "HEAD") != old_head or git(
                root, "status", "--porcelain", "--untracked-files=normal"
            ):
                return UpdateResult(
                    "deferred",
                    "The checkout changed while preparing the update. Keeping your changes and installed version.",
                )
            step = "Activating the update"
            old_state = read_state(root)
            # Verify pointer persistence before changing tracked files. The update lock keeps
            # new app processes out until both pointer and fast-forward have completed.
            write_state(
                root, {"commit": target, "environment": str(environment.relative_to(directory))}
            )
            try:
                git(root, "merge", "--ff-only", "--no-edit", target)
            except BaseException:
                if old_state is None:
                    (directory / "active.json").unlink(missing_ok=True)
                else:
                    write_state(root, old_state)
                raise
            activated = True
            return UpdateResult(
                "updated", f"Updated MarianaBot to {target[:8]}. Opening the new version."
            )
    except Timeout:
        return UpdateResult(
            "deferred",
            "Update deferred while MarianaBot is running. Close chat and Discord after research stops; the next start will update.",
        )
    except (UpdateFailure, OSError, ValueError, zipfile.BadZipFile):
        return UpdateResult(
            "unavailable",
            f"{step} did not finish. Opening the installed version; the next start will retry.",
        )
    finally:
        active = read_state(root)
        referenced = active and (directory / active["environment"]).parent == candidate
        if candidate is not None and not activated and not referenced:
            # Only remove the newly created candidate, never a prior runtime or research folder.
            resolved = candidate.resolve()
            if resolved.parent == (directory / "installs").resolve() and not candidate.is_symlink():
                shutil.rmtree(resolved, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Check and install MarianaBot updates safely")
    parser.add_argument("--check", action="store_true", help="Check only; do not install")
    parser.add_argument("--startup", action="store_true", help="Respect startup update preferences")
    parser.add_argument("--config", type=Path, default=Path("mariana.toml"))
    parser.add_argument("--data-dir", type=Path, default=Path(".mariana"))
    args = parser.parse_args()
    root = installation_root()
    if root is None:
        print(
            "Automatic updates are available for Git checkouts. Update this package with your installer."
        )
        return
    if args.startup and not automatic_enabled(args.config):
        return
    print(
        update(root, config=args.config, data_dir=args.data_dir, check_only=args.check).message,
        flush=True,
    )


if __name__ == "__main__":
    main()
