"""Optional Gateway adapter. Imported only by the explicit Discord run command."""

import asyncio
import contextlib
import logging

import discord
from discord import app_commands

from marianabot.discord_bridge import DiscordBridge, excerpt

log = logging.getLogger(__name__)


def render_embed(data: dict) -> discord.Embed:
    """Escape saved output and enforce Discord's limits after escaping."""

    def clean(value, limit):
        return excerpt(discord.utils.escape_markdown(str(value)), limit)

    title = clean(data.get("title", "MarianaBot"), 256)
    description = clean(data.get("description", ""), 900)
    footer = clean(data.get("footer", {}).get("text", ""), 250)
    remaining = 5800 - len(title) - len(description) - len(footer)
    embed = discord.Embed(title=title, description=description, color=data.get("color", 0x7AA2F7))
    for field in data.get("fields", [])[:25]:
        if remaining < 100:
            break
        name = clean(field["name"], 120)
        value = clean(field["value"], min(1024, remaining - len(name)))
        remaining -= len(name) + len(value)
        embed.add_field(name=name, value=value, inline=bool(field.get("inline")))
    embed.set_footer(text=footer)
    return embed


def fallback_text(embed: discord.Embed) -> str:
    lines = [f"**{embed.title}**", embed.description or ""]
    lines += [f"**{field.name}**\n{field.value}" for field in embed.fields]
    lines.append(embed.footer.text or "")
    return excerpt("\n\n".join(line for line in lines if line), 1950)


class ResearchCommands(app_commands.Group):
    def __init__(self, bridge: DiscordBridge):
        super().__init__(
            name="mariana", description="Your personal research workspace", guild_only=True
        )
        self.bridge = bridge

    async def dispatch(self, interaction: discord.Interaction, action: str, **kwargs):
        try:
            self.bridge.config.authorize(
                interaction.guild_id, interaction.channel_id, interaction.user.id
            )
        except PermissionError:
            await interaction.response.send_message(
                "This command is not available here or for this user.", ephemeral=True
            )
            return
        # Acknowledge before starting a worker, which can take several seconds.
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.bridge.command(
                guild=interaction.guild_id,
                channel=interaction.channel_id,
                user=interaction.user.id,
                request_id=interaction.id,
                action=action,
                **kwargs,
            )
        except PermissionError as exc:
            result = str(exc)
        except Exception as exc:
            log.error(
                "Discord command failed (%s); inspect local state before retrying.",
                type(exc).__name__,
            )
            result = "The command could not be completed. Check the local worker and /mariana status before retrying."
        await interaction.edit_original_response(
            content=excerpt(discord.utils.escape_markdown(result), 1950),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(description="List recent research stored on this bot's host")
    async def sessions(self, interaction: discord.Interaction):
        await self.dispatch(interaction, "sessions")

    @app_commands.command(
        description="Connect one run to this channel; share future round updates and MB replies"
    )
    async def watch(self, interaction: discord.Interaction, run_id: str):
        await self.dispatch(interaction, "watch", run_id=run_id)

    @app_commands.command(description="Stop sharing updates; leave research running")
    async def unwatch(self, interaction: discord.Interaction):
        await self.dispatch(interaction, "unwatch")

    @app_commands.command(description="Show the watched run's status, usage and memory")
    async def status(self, interaction: discord.Interaction):
        await self.dispatch(interaction, "status")

    @app_commands.command(
        description="Ask the master brain; its reply will be posted to this channel"
    )
    async def ask(
        self, interaction: discord.Interaction, message: app_commands.Range[str, 1, 4000]
    ):
        await self.dispatch(interaction, "ask", message=message)

    @app_commands.command(
        description="Change the brief at the next round boundary; does not resume paused research"
    )
    async def steer(
        self, interaction: discord.Interaction, message: app_commands.Range[str, 1, 4000]
    ):
        await self.dispatch(interaction, "steer", message=message)

    @app_commands.command(description="Pause research and save completed work")
    async def pause(self, interaction: discord.Interaction):
        await self.dispatch(interaction, "pause")

    @app_commands.command(
        description="Resume the watched research using its saved model and billing settings"
    )
    async def resume(self, interaction: discord.Interaction):
        await self.dispatch(interaction, "resume")

    @app_commands.command(
        description="Start a reply worker for unanswered MB questions without resuming research"
    )
    async def retry(self, interaction: discord.Interaction):
        await self.dispatch(interaction, "retry")

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        log.error("Discord interaction failed (%s).", type(error).__name__)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Command unavailable. Check local bot status.", ephemeral=True
            )


class MarianaDiscord(discord.Client):
    def __init__(self, bridge: DiscordBridge):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.bridge = bridge
        self.tree = app_commands.CommandTree(self)
        self.destination = None
        self.embed_enabled = True
        self.monitor_task = None

    async def setup_hook(self):
        guild = discord.Object(id=self.bridge.config.guild_id)
        self.tree.add_command(ResearchCommands(self.bridge), guild=guild)
        await self.tree.sync(guild=guild)
        self.monitor_task = asyncio.create_task(self.monitor())

    async def on_ready(self):
        try:
            channel = await self.fetch_channel(self.bridge.config.channel_id)
            if (
                not isinstance(channel, discord.TextChannel)
                or channel.guild.id != self.bridge.config.guild_id
            ):
                raise ValueError(
                    "Configured destination is not a text channel in the configured server"
                )
            permissions = channel.permissions_for(channel.guild.me)
            if not permissions.view_channel or not permissions.send_messages:
                raise ValueError("Missing View Channel or Send Messages permission")
            self.destination = channel
            self.embed_enabled = permissions.embed_links
            if not self.embed_enabled:
                log.warning(
                    "Embed Links permission is missing; research updates will use formatted text."
                )
            print(
                "Discord connected. Research cards "
                + ("enabled" if self.embed_enabled else "using text fallback")
                + ". Use /mariana sessions, then /mariana watch to select research.",
                flush=True,
            )
        except Exception as exc:
            log.error(
                "Discord destination unavailable (%s). Check IDs and permissions locally.",
                type(exc).__name__,
            )
            await self.close()

    async def send_update(self, body: str, *, embed: dict | None = None):
        if self.destination is None:
            raise ValueError("Discord destination is not ready")
        if embed is not None:
            rendered = render_embed(embed)
            if hasattr(self.destination, "permissions_for"):
                self.embed_enabled = self.destination.permissions_for(
                    self.destination.guild.me
                ).embed_links
            if self.embed_enabled:
                await self.destination.send(
                    embed=rendered, allowed_mentions=discord.AllowedMentions.none()
                )
                return
            await self.destination.send(
                fallback_text(rendered),
                allowed_mentions=discord.AllowedMentions.none(),
                suppress_embeds=True,
            )
            return
        # Pending notifications from older versions still deliver as plain text.
        body = excerpt(discord.utils.escape_markdown(body), 1950)
        await self.destination.send(
            body, allowed_mentions=discord.AllowedMentions.none(), suppress_embeds=True
        )

    async def monitor(self):
        await self.wait_until_ready()
        while not self.is_closed():
            if self.destination is not None:
                try:
                    await self.bridge.deliver(self.send_update)
                except Exception as exc:
                    log.error(
                        "Discord update delivery failed (%s); queued updates retained.",
                        type(exc).__name__,
                    )
            await asyncio.sleep(self.bridge.config.poll_seconds)

    async def close(self):
        if self.monitor_task and self.monitor_task is not asyncio.current_task():
            self.monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.monitor_task
        await super().close()
