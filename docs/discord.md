# Optional Discord connection

Connect your own bot to one private text channel to follow research from your
phone. Round updates include the work summary, changes, next direction, review
score and blockers, elapsed time, reported usage/reset information, the app's
context budget and completed compactions. You can also ask the master brain,
steer the brief, pause and resume.

This integration is optional. A normal install does not include the Discord SDK,
open a Discord connection or share research. Each person creates their own bot,
uses their own credentials and runs it beside their own MarianaBot installation.
There is no shared bot operated by this repository's author.

## 1. Create your bot and private channel

1. Open the [Discord Developer Portal](https://discord.com/developers/applications)
   and create an application. Give it a name you recognize, such as MarianaBot.
2. Open **Bot**. Generate/reset its bot token and keep it private. This is a bot
   token, not your Discord password or personal user token. Do not post it in
   ChatGPT, Discord chat, GitHub issues or screenshots. Disable Public Bot if the
   portal offers that setting. Leave all privileged Gateway intents off; Message
   Content, Server Members and Presence are not required.
3. Configure a **Guild Install** with scopes `bot` and `applications.commands`.
   Grant **View Channels** and **Send Messages**. Do not grant Administrator.
   Use the portal's install link to add the bot to your own Discord server.
4. Create a private standard text channel. Allow only yourself, anyone you
   deliberately trust, and the bot to view it. Grant the bot View Channel and
   Send Messages there. Allow your account to use application commands.
5. In Discord settings, enable **Advanced → Developer Mode**. Right-click the
   server, channel and your user profile to copy their IDs. These are numeric IDs,
   not names. Keep them for the local setup wizard.

Portal labels can change; Discord's [official setup guide](https://docs.discord.com/developers/quick-start/getting-started)
describes the current application and installation screens. Leave **Interactions
Endpoint URL** empty: MarianaBot receives slash commands over Discord's outbound
[Gateway connection](https://docs.discord.com/developers/interactions/overview).
No public HTTP endpoint or new inbound VPS port is needed.

## 2. Configure the host that holds your research

Run these commands in the MarianaBot checkout, using its activated virtual
environment. On Windows, executable paths are `.venv\Scripts\python.exe` and
`.venv\Scripts\mariana.exe`. On Linux they are `.venv/bin/python` and
`.venv/bin/mariana`.

```text
python -m pip install -e ".[discord]"
mariana discord setup
mariana discord status
```

For the VPS layout in the [server guide](cloud-server.md), use:

```bash
cd ~/MarianaBot
.venv/bin/python -m pip install -e '.[discord]'
.venv/bin/mariana discord setup --data-dir ~/.local/share/marianabot
.venv/bin/mariana discord status
```

The wizard asks for your server ID, private channel ID and a comma-separated
list of permitted user IDs. It separately asks whether remote controls should be
enabled and whether the integration itself should be enabled. Both default to
**no**. Enable them when ready to use the corresponding features.

Optionally enter the bot token into the wizard's hidden local prompt. It goes
into `discord-token.txt`, never into the settings or research database. On
Linux this file is created with owner-only permissions; on Windows keep the
checkout in a private user folder and restrict its Windows file permissions.
Alternatively, supply the token through `MARIANA_DISCORD_BOT_TOKEN` using your
usual private environment/secret manager. Never put it directly in shell command
arguments or a committed script. The environment token takes precedence over the
file and is removed from worker and model-client environments.

`discord.toml` stores your choices and the absolute research directory. The
settings file, token file and research are ignored by Git. Setup never overwrites
existing files; edit your private settings to change them, then restart the bot.
An empty user list is invalid. Changing the destination or access policy requires
selecting a run again. Custom config paths are supported with `--config`; keep
any renamed private files outside the repository or add your own Git exclusion.

The offline `status` command checks configuration and token presence; it does
not verify the token with Discord or send a message.

## 3. Connect and choose research

```text
mariana discord run
```

Keep this process running alongside MarianaBot. It uses the configured research
directory; **the bot and terminal chat must use the same directory on the same
host**. It cannot discover research on another computer. One bot process may own
a research directory at a time.

In your private Discord channel:

```text
/mariana sessions
/mariana watch run_id:YOUR_RUN_ID
/mariana status
/mariana ask message:What changed in the last round?
/mariana steer message:Limit the pilot to EUR 500 and interview five customers.
/mariana pause
/mariana resume
/mariana unwatch
```

Choose slash commands from Discord's command picker; these are structured
commands, not ordinary chat messages. Commands are registered only in your
configured server. Every request checks the server, channel and user allowlist
again on the host. DMs, other channels and other users are refused. A user who
can read the channel can read posted research even if they cannot control it.

`watch` explicitly connects one run to the channel. It posts future completed
rounds, MB replies and state changes, including locally initiated MB dialogue.
It does not backfill an entire historical transcript or subscribe to new runs
automatically. `unwatch` drops unsent updates and leaves research running.
Messages already sent to Discord remain there.

Command acknowledgements are visible only to the caller. **MB answers and
research updates are posted to the configured channel.** Long content is
excerpted and labelled; full material remains in MarianaBot and its exports.
Mentions and link previews are disabled for posted research.

`ask` starts an MB reply worker if needed, including for a paused or completed
run. It does not resume research. `steer` queues a brief change for the next round
boundary and leaves a paused run paused. `pause` needs no model call. `resume`
uses the run's saved models and billing controls. MB requests still consume the
research provider's subscription allowance and may wait for a reset. Use
`/mariana retry` if an MB question was saved but its worker could not start.
Start new problems, change models, export files and permanently stop research
in the main application; Discord does not expose shell commands or file access.

## Keep it running on the VPS

With the terminal service from the server guide already installed, start the
bot inside that service's tmux server. This keeps Discord-started workers under
the same persistent service as terminal-started workers:

```bash
systemctl --user start marianabot-terminal
tmux -L marianabot new-session -d -s mariana-discord \
  'cd ~/MarianaBot && exec .venv/bin/mariana discord run --data-dir ~/.local/share/marianabot'
```

Inspect its console with `tmux -L marianabot attach -t mariana-discord`; detach
with Ctrl+B, then D. Closing SSH leaves it running. To stop only the bot,
attach and press Ctrl+C. Research workers remain managed by the terminal service.
Do not run a second copy on your laptop using the same token.

This initial integration does **not** install a Discord autostart service. Start
the bot session again after a VPS reboot or terminal-service restart. Research
recovery follows the existing server rules independently. Subscription resets
and native client outages do not depend on Discord being online.

## Delivery and metric limits

The bot checks saved state every 15 seconds by default. It creates summaries
from the research chair's existing output and judge's next prompt, without an
extra model request. Older plans lacking summary sections use a labelled excerpt.
Round notifications include the latest available status snapshot at collection.

Usage is reported by the native clients. Unknown allowance or reset times remain
unknown, and incomplete token totals are labelled partial. Token totals belong
to the selected run; allowance snapshots describe the shared provider account.
Elapsed time includes pauses and quota waits. The context budget is MarianaBot's
configured **character** budget, not a measurement of the provider's remaining
token window. Saved-summary size is only one part of the working context.

Notifications and delivery cursors are stored in SQLite. Failed sends remain
queued for retry, while research continues independently. A crash after Discord
accepts a message but before the local delivery record commits can produce a
duplicate notification. Command interaction IDs are recorded before side effects,
so redelivery does not blindly enqueue another paid model request. If a crash
leaves a command's outcome uncertain, check status before issuing a new command.

The integration has offline authorization, queue/restart, SDK-adapter and worker
tests. It has not yet been tested against a real configured Discord bot. Begin
with offline demo research before sharing private material or enabling controls.
For a local preview without Discord or its SDK:

```text
mariana discord preview RUN_ID
```
