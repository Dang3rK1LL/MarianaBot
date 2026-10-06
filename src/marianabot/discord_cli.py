"""Explicit optional setup and launch; base installation does not import discord.py."""

import json
import logging
import uuid
from pathlib import Path

import typer
from filelock import FileLock, Timeout
from rich.console import Console
from rich.text import Text

from marianabot.discord_bridge import DiscordBridge, status_text
from marianabot.discord_config import DiscordConfig, load_discord, read_token, write_private
from marianabot.store import Store

app = typer.Typer(
    help="Optional Discord updates and remote controls. Never starts automatically.",
    pretty_exceptions_enable=False,
)
console = Console()


def error(message: str):
    console.print(Text(message, style="red"))
    raise typer.Exit(2)


@app.command()
def setup(config: Path = Path("discord.toml"), data_dir: Path = Path(".mariana")):
    """Create private settings for your own bot, server, channel and allowed users."""
    if config.exists():
        error(
            "Discord settings already exist. Edit that private file; setup will not overwrite it."
        )
    console.print("[bold]Connect your own Discord bot[/bold]")
    console.print("Create a bot and private server channel first: docs/discord.md.")
    console.print(
        "Watched research summaries and MB replies will be sent to Discord. Every channel member can read them."
    )
    token_path = config.resolve().parent / "discord-token.txt"
    try:
        guild = typer.prompt("Discord server ID", type=int)
        channel = typer.prompt("Private text channel ID", type=int)
        users = typer.prompt("Allowed Discord user IDs, separated by commas")
        control = typer.confirm(
            "Allow these users to ask MB, steer, pause and resume research?", default=False
        )
        enabled = typer.confirm(
            "Enable this integration when you explicitly run the bot?", default=False
        )
        settings = DiscordConfig(
            guild_id=guild,
            channel_id=channel,
            allowed_user_ids=[int(v.strip()) for v in users.split(",")],
            allow_control=control,
            enabled=enabled,
            data_dir=data_dir.resolve(),
            token_file=token_path,
        )
        token = ""
        if not token_path.exists() and typer.confirm(
            "Save the bot token in a private local file? (Otherwise use MARIANA_DISCORD_BOT_TOKEN.)",
            default=False,
        ):
            token = typer.prompt(
                "Bot token (hidden; never paste it into chat or GitHub)", hide_input=True
            ).strip()
            if not token or any(c.isspace() for c in token):
                raise ValueError("Invalid token format")
        # All prompts and validation finish before files are created.
        if token:
            write_private(token_path, token + "\n")
        contents = "# Private optional integration. Do not commit this file.\n"
        for key, value in settings.model_dump(mode="json").items():
            contents += f"{key} = {json.dumps(value)}\n"
        write_private(config, contents)
    except (ValueError, OSError):
        error(
            "Setup could not be saved. Check IDs, paths and file permissions; no credentials were printed."
        )
    console.print(Text(f"Saved {config.resolve()}. Research store: {settings.data_dir}"))
    console.print(
        "Nothing has connected or been sent. Next: mariana discord status, then mariana discord run."
    )
    console.print(
        "Keep these files in your private account folder. On Windows, file access follows that folder's permissions."
    )


@app.command()
def token(config: Path = Path("discord.toml"), replace: bool = False):
    """Save your bot token through a hidden prompt on the bot's host."""
    try:
        settings = load_discord(config)
        if settings.token_file.exists() and not replace:
            error(
                "A token file already exists. Run this command with --replace to enter a new one privately."
            )
        value = typer.prompt("Discord bot token (hidden)", hide_input=True).strip()
        if len(value) < 20 or any(
            ord(character) <= 32 or ord(character) >= 127 for character in value
        ):
            error(
                "A complete token was not received. Use the Windows setup dialog's Paste button, or paste again locally."
            )
        if replace:
            temporary = settings.token_file.with_name(f".discord-token-{uuid.uuid4().hex}.token")
            try:
                write_private(temporary, value + "\n")
                temporary.replace(settings.token_file)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            write_private(settings.token_file, value + "\n")
    except (ValueError, OSError):
        error("Token could not be saved. Check configuration and file permissions locally.")
    console.print("Bot token saved privately. No token was printed.")


@app.command()
def status(config: Path = Path("discord.toml"), data_dir: Path | None = None):
    """Check private configuration and token presence without contacting Discord."""
    try:
        settings = load_discord(config)
        if data_dir is not None:
            settings.data_dir = data_dir.resolve()
        read_token(settings)
    except (ValueError, OSError):
        error(
            "Configuration or token unavailable. Run mariana discord setup; verify local file permissions."
        )
    console.print(
        f"Configured; enabled={settings.enabled}; remote control={settings.allow_control}."
    )
    console.print(Text(f"Research store: {settings.data_dir}"))
    console.print(
        "Token present (not verified). This offline check does not establish a live Discord connection."
    )


@app.command()
def preview(run_id: str, data_dir: Path = Path(".mariana")):
    """Preview a status update locally. No bot, token, SDK or network required."""
    store = Store(data_dir)
    try:
        console.print(Text(status_text(store, run_id)))
    except ValueError as exc:
        error(str(exc))
    finally:
        store.close()


@app.command()
def run(
    config: Path = Path("discord.toml"),
    data_dir: Path | None = None,
    notifications_only: bool = False,
):
    """Connect the optional bot. Research and chat share the selected local store."""
    try:
        settings = load_discord(config)
        if notifications_only and settings.allow_control:
            error(
                "This service requires allow_control = false. Use the managed tmux setup for remote controls."
            )
        if not settings.enabled:
            error(
                "Discord is disabled. Set enabled = true in your private Discord settings when ready."
            )
        if data_dir is not None:
            settings.data_dir = data_dir.resolve()
        token = read_token(settings)
    except (ValueError, OSError):
        error("Configuration or token unavailable. Check mariana discord status.")
    try:
        from marianabot.discord_bot import MarianaDiscord
    except ImportError:
        error('Install the optional integration first: python -m pip install -e ".[discord]"')
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(settings.data_dir / "discord.lock"), timeout=0):
            store = Store(settings.data_dir)
            try:
                logging.basicConfig(level=logging.WARNING, format="%(message)s")
                # SDK debug logs can contain interaction details; keep only our redacted diagnostics.
                sdk_logger = logging.getLogger("discord")
                sdk_logger.handlers = [logging.NullHandler()]
                sdk_logger.propagate = False
                MarianaDiscord(DiscordBridge(store, settings)).run(token, log_handler=None)
            finally:
                store.close()
    except Timeout:
        error("A Discord process is already using this research directory.")
    except Exception as exc:
        error(
            f"Discord stopped ({type(exc).__name__}). Check token, network and channel permissions locally."
        )
