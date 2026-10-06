"""Private, opt-in Discord settings. Never contains a bot token."""

import hashlib
import json
import os
import tomllib
from pathlib import Path
from typing import Annotated

from pydantic import Field

from marianabot.config import StrictModel

Snowflake = Annotated[int, Field(strict=True, gt=0, lt=2**64)]


class DiscordConfig(StrictModel):
    enabled: bool = False
    guild_id: Snowflake
    channel_id: Snowflake
    allowed_user_ids: list[Snowflake] = Field(min_length=1, max_length=20)
    allow_control: bool = False
    data_dir: Path = Path(".mariana")
    token_file: Path = Path("discord-token.txt")
    poll_seconds: int = Field(default=15, ge=5, le=300)

    @property
    def scope(self) -> str:
        # Changing the destination or access policy requires an explicit new /watch.
        policy = (self.guild_id, self.channel_id, sorted(self.allowed_user_ids), self.allow_control)
        return hashlib.sha256(json.dumps(policy).encode()).hexdigest()[:24]

    def authorize(self, guild: int | None, channel: int | None, user: int):
        if (
            not self.enabled
            or guild != self.guild_id
            or channel != self.channel_id
            or user not in self.allowed_user_ids
        ):
            raise PermissionError("This command is not available here or for this user.")


def load_discord(path: Path) -> DiscordConfig:
    config = DiscordConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    config.data_dir = (path.resolve().parent / config.data_dir).resolve()
    config.token_file = (path.resolve().parent / config.token_file).resolve()
    return config


def read_token(config: DiscordConfig) -> str:
    token = os.environ.get("MARIANA_DISCORD_BOT_TOKEN", "").strip()
    if not token:
        if not config.token_file.is_file():
            raise ValueError(
                "Bot token missing. Run mariana discord setup or set MARIANA_DISCORD_BOT_TOKEN."
            )
        if os.name != "nt" and config.token_file.stat().st_mode & 0o077:
            raise ValueError("The bot token file must be private: chmod 600 on that file.")
        token = config.token_file.read_text(encoding="utf-8").strip()
    if not token or any(ord(character) <= 32 or ord(character) >= 127 for character in token):
        raise ValueError("Bot token is empty or contains invalid characters. Replace it locally.")
    return token


def write_private(path: Path, contents: str):
    """Create, never overwrite, a file restricted to its owner on POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(contents)
