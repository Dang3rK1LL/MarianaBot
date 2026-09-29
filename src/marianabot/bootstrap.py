"""Choose a validated runtime before importing application or provider code."""

import os
import subprocess
import sys
from pathlib import Path

from filelock import Timeout

from marianabot.runtime import installation_lease, installation_root
from marianabot.updater import automatic_enabled, python_in, selected_environment, update


def option(args: list[str], name: str, default: str) -> Path:
    for index, value in enumerate(args):
        if value.startswith(name + "="):
            return Path(value.split("=", 1)[1])
        if value == name and index + 1 < len(args):
            return Path(args[index + 1])
    return Path(default)


def is_start(args: list[str]) -> bool:
    if any(value in args for value in ("--help", "--demo")) or args[:1] == ["demo"]:
        return False
    return (
        not args
        or args[0] in {"chat", "run", "resume", "--connect-server"}
        or args[:2] == ["discord", "run"]
    )


def main():
    args = sys.argv[1:]
    no_update = args[:1] == ["--no-update"]
    args = args[1:] if no_update else args
    skip_once = os.environ.pop("MARIANA_SKIP_UPDATE_ONCE", "") == "1"
    root = installation_root()
    config = option(args, "--config", "mariana.toml")
    data_dir = option(args, "--data-dir", ".mariana")
    # Discord has its own --config option; application update preferences remain in mariana.toml.
    if args[:1] == ["discord"]:
        config = Path("mariana.toml")
    if root and is_start(args) and not no_update and not skip_once and automatic_enabled(config):
        result = update(
            root, config=config, data_dir=data_dir, emit=lambda text: print(text, flush=True)
        )
        print(result.message, flush=True)
    # Explicit updates must run outside a runtime lease of their own.
    if args[:1] == ["update"]:
        from marianabot.updater import main as update_main

        sys.argv = [sys.argv[0], *args[1:]]
        update_main()
        return
    try:
        with installation_lease(root):
            environment = selected_environment(root) if root else None
            if environment and os.path.normcase(os.path.abspath(sys.prefix)) != os.path.normcase(
                str(environment)
            ):
                child_env = dict(
                    os.environ, MARIANA_INSTALL_ROOT=str(root), MARIANA_SKIP_UPDATE_ONCE="1"
                )
                # Wait as the launcher lease keeps this version stable until the child exits.
                result = subprocess.call(
                    [str(python_in(environment)), "-m", "marianabot", *args], env=child_env
                )
                raise SystemExit(result)
            if root:
                os.environ["MARIANA_INSTALL_ROOT"] = str(root)
            if args[:1] == ["--connect-server"]:
                from marianabot.connect_server import main as connect

                raise SystemExit(connect())
            if args[:1] == ["--service"]:
                from marianabot.server import main as service

                sys.argv = [sys.argv[0], *args[1:]]
                service()
                return
            from marianabot.cli import app

            app(args=args)
    except Timeout:
        print(
            "Another startup is still updating MarianaBot. Please start again after it finishes.",
            flush=True,
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
