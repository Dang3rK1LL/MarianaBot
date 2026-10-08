# Optional Discord connection

Connect your own bot to one private text channel to follow research from your
phone. Research updates use clean, colored cards with the current question,
stage, findings, changes, next direction and reported usage. Each round shows
independent research, plan synthesis, independent critique and the review decision.
Every card includes the research ID. New research uses readable random IDs such
as `MB-7K3M-9Q2R-5V8N`; existing IDs remain valid. `/mariana status` includes
allowance and context information. You can ask MB, steer the brief, pause,
resume, retrieve the latest recap, or permanently stop a specific run.

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
   Grant **View Channels**, **Send Messages** and **Embed Links**. Do not grant Administrator.
   Use the portal's install link to add the bot to your own Discord server.
4. Create a private standard text channel. Allow only yourself, anyone you
   deliberately trust, and the bot to view it. Grant the bot View Channel and
   Send Messages and Embed Links there. Allow your account to use application commands.
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
Set `auto_watch_new = true` to automatically share research created after the
bot first starts with this option. It defaults to false and does not share older
research. Set `allow_control = true` to enable the listed research controls for
the allowed accounts.
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

Bot startup follows MarianaBot's [automatic update settings](updates.md). A
running bot defers application updates until it exits. Its private configuration
and token are preserved; an update does not enable a disabled integration.

In your private Discord channel:

```text
/mariana sessions
/mariana watch run_id:YOUR_RUN_ID
/mariana status
/mariana recap
/mariana help
/mariana ask message:What changed in the last round?
/mariana steer message:Keep the EUR 500 budget and validate with public online sources.
/mariana pause
/mariana resume
/mariana stop run_id:YOUR_RUN_ID confirm:true
/mariana unwatch
```

Choose slash commands from Discord's command picker; these are structured
commands, not ordinary chat messages. Commands are registered only in your
configured server. Every request checks the server, channel and user allowlist
again on the host, including before showing autocomplete suggestions. A user who
can read the channel can read posted research even if they cannot control it.
DMs, other channels and other users are refused.

The `run_id` field suggests IDs with their topics and status. Search by ID or
topic, or paste the exact ID; matching is case insensitive. Status, recap,
questions, steering, pause, resume and retry accept an optional ID. Without it,
they target the selected research. Controls name their target in the response.
`stop` requires an explicit ID and `confirm:true`; omitting confirmation changes
nothing. A stopped run cannot resume, and its saved work remains available.

`watch` explicitly connects one run to the channel. It posts future research stages,
completed rounds, MB replies and state changes, including locally initiated MB dialogue.
It does not backfill an entire historical transcript. With `auto_watch_new`, new
research gets a creation card and its own durable updates, including if it
finishes between polls or the bot restarts. The newest run becomes the default
command target. Manually using `watch` selects one run and disconnects the
others; subsequent new research is still followed automatically. `unwatch`
disconnects current updates, drops unsent posts and leaves research running.
Automatic following starts again for the next newly created run.
Messages already sent to Discord remain there.

Command acknowledgements are visible only to the caller. **MB answers and
research updates are posted to the configured channel.** Long content is
excerpted and labelled; full material remains in MarianaBot and its exports.
Mentions are disabled and model formatting is escaped. Cards have no automatic
link previews or attached files. If Embed Links is unavailable, updates fall back
to formatted text.

### What appears during research

- **Research created:** the ID, problem and selected models when automatic following is enabled.
- **Preparing the research brief / Research brief ready:** MB defines the
  objective, constraints, assumptions and evidence needed.
- **Round started:** the research question, current focus, previous review and
  blockers, specialist roles, model settings and retrieval availability.
- **Plan synthesis:** independent proposals are saved and the research chair is
  comparing recommendations and assembling a plan.
- **Independent critique:** the new plan's findings and proposed changes, plus
  the judges' roles and what they are checking.
- **Review decision:** short excerpts of the saved critiques while the review
  chair weighs objections and determines the verdict.
- **Round complete:** recap, changes, next research priority, review score and its
  change, open issues, strengths, human tests and retained disagreements.
  Timing and tokens appear in separate compact fields.
- **Research waiting / continuing:** waits of at least one minute and automatic
  continuation when they end. Brief request-spacing delays do not produce posts.
- **Paused / complete / stopped:** the saved reason, next direction and any
  next online checks and evidence limitations. MB answers also use cards.

The feed posts at stage boundaries rather than after every individual agent call.
It uses saved research output and does not make extra model requests to write
Discord summaries. Long sections use labelled excerpts; full output remains in
MarianaBot and its exports. Offline fixtures are clearly marked in card footers.

`ask` starts an MB reply worker if needed, including for a paused or completed
run. It does not resume research. `steer` queues a brief change for the next round
boundary and leaves a paused run paused. `pause` needs no model call. `resume`
uses the run's saved models and billing controls. MB requests still consume the
research provider's subscription allowance and may wait for a reset. Use
`/mariana retry` if an MB question was saved but its worker could not start.
`recap` reads the latest saved round without requesting a new model response.
Start new problems, choose work folders, change models and export files in the
main application; Discord does not expose shell commands or file access.

## Keep it running on the VPS

With the terminal service from the server guide already installed, enable the
managed Discord service:

```bash
cp deploy/marianabot-discord.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now marianabot-discord.service
```

This service supervises a `mariana-discord` session in the terminal service's
tmux server. Bot-started workers stay under the terminal service, so restarting
Discord keeps them running. It starts at boot with user lingering enabled,
restarts on failure and supports controls when `allow_control = true`. It
requires the terminal service and both private settings and token files.

```bash
systemctl --user restart marianabot-discord.service
systemctl --user stop marianabot-discord.service
```

Inspect its console with `tmux -L marianabot attach -t mariana-discord`; detach
with Ctrl+B, then D. Stop it with systemctl; closing its pane directly makes the
supervisor restart it. Do not run a second bot copy in another tmux session or
on your laptop. Research recovery follows the existing server rules when the
terminal service restarts.

For a foreground install that only sends updates,
`mariana discord run --notifications-only` still refuses `allow_control = true`.

When configuration is already prepared, `mariana discord token` saves the token
through a hidden prompt. Use `--replace` to replace a saved token atomically.
The Windows
`MarianaBot-Discord-Setup.cmd` shortcut uses `mariana-server.json` to open a
masked Windows token dialog. Its Paste button and Ctrl+V accept clipboard input.
It verifies the bot and channel, saves the token on Ubuntu through SSH without
a local token file, and starts the installed Discord service.

## Delivery and metric limits

The bot checks saved state every 15 seconds by default. It creates summaries
from the research chair's existing output and judge's next prompt, without an
extra model request. Older plans lacking summary sections use a labelled excerpt.
New round recaps retain separate token totals for the round's RB/JB calls and the
whole run, along with elapsed time at completion. Later rounds cannot inflate an
earlier recap. Starts, intermediate stages, MB messages and recaps are collected
in order, including when a round finishes between polling checks.
Repeated stages after checkpoint recovery are deduplicated. Round duration
includes pauses and quota waits. Legacy checkpoints without timing use the
latest available snapshot at collection.

Round-team tokens include the selected round's RB/JB calls and retries. Run totals
also include MB and context compaction calls. Cache-read/write tokens are shown
when reported and are already included in input totals; they are not added again.
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

The integration has authorization, ordered stage/MB delivery, queue migration,
restart, SDK-adapter, message-size and worker tests. You can start with offline
demo research before sharing private material or enabling controls.
For a local preview without Discord or its SDK:

```text
mariana discord preview RUN_ID
```

To preview the card layout with synthetic data and no Discord connection:

```text
python scripts/preview_discord.py
```

Open `.mariana/visual-review/discord-updates.html` locally. This preview does not
start research or contact model providers.
With `resvg-py` installed, the script also writes a PNG of the round recap.
See the [sample recap](assets/discord.png); it uses the current embed builder
and synthetic data.
