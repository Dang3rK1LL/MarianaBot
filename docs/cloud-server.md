# MarianaBot on a personal Ubuntu VPS

The server runs MarianaBot and the official Codex and Claude Code clients.
Sign in with your existing subscriptions. No paid API project or public web
application is required. Provider limits and model entitlements still apply.

## Everyday use

On the configured Windows laptop, double-click **MarianaBot-Server.cmd**.
This opens the server's chat over SSH. Type or paste your business problem;
the usage dashboard, model selector, memory and slash commands work as on the laptop.
`/load` reads a file on the server. Exports also stay on the server until downloaded.

Closing the terminal disconnects your screen; the chat and workers remain on the
server. Reopen the shortcut to reattach. `/quit` closes the chat window while
research continues. `/pause` stops research at its saved checkpoints. `/resume`
continues it. Reconnecting from two terminals shows the same screen.

New live research asks for a work folder on Ubuntu before starting. The default
is `~/MarianaBot-work`; each run gets a new `research-RUN_ID` folder. These paths
are on the server, even when you are using the Windows shortcut. Reports refresh
after each completed round and remain available after disconnecting.
Research IDs use a readable random format such as `MB-7K3M-9Q2R-5V8N`.

A dedicated systemd user service owns the tmux server and all its research
processes. The optional [Discord service](discord.md#keep-it-running-on-the-vps)
also launches its bot there, so restarting Discord preserves research workers.
At terminal service startup, interrupted research is recovered from SQLite.
An explicit pause/stop, a provider/login error, completed research, and pending
MB-only replies are **not** automatically resumed. An unexpected failure within
the research worker can still require `/resume`; the service restarts the terminal
server if that server fails. A crashed worker is recovered when the service next starts.

## First installation

These paths assume the normal account's checkout is `~/MarianaBot`.
Use a non-root SSH account with sudo. Start with at least 4 GB RAM and low agent
concurrency. The models run at their providers; no GPU is needed.

```bash
sudo apt-get update
sudo apt-get install -y python3-venv git curl ca-certificates tmux
git clone https://github.com/Dang3rK1LL/MarianaBot.git ~/MarianaBot
cd ~/MarianaBot
python3 -m venv .venv
.venv/bin/python -m pip install -e .
curl -fsSL https://chatgpt.com/codex/install.sh | sh
curl -fsSL https://claude.ai/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
codex login --device-auth
claude auth login --claudeai
.venv/bin/mariana setup --data-dir ~/.local/share/marianabot
```

Finish each login in your own browser. Paste any returned Claude login code into
the SSH terminal that started that login. Never put credentials in this repository.
Codex device login may need enabling in ChatGPT's security settings.

Review `mariana.toml` and the [subscription settings](subscriptions.md), including
the existing provider-side overage controls. `overage_disabled = true` is your
attestation, not a remote billing switch. Preserve the same disabled-credit settings
when moving from a laptop. Do not run research simultaneously on both machines if
you want one coordinator for your allowance.

```bash
.venv/bin/mariana doctor --data-dir ~/.local/share/marianabot
.venv/bin/mariana demo --plain --data-dir ~/.local/share/marianabot-demo
mkdir -p ~/.config/systemd/user ~/.local/bin
cp deploy/marianabot-terminal.service deploy/marianabot-backup.service \
   deploy/marianabot-backup.timer ~/.config/systemd/user/
install -m 755 deploy/mariana-linux.sh ~/.local/bin/mariana
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now marianabot-terminal.service marianabot-backup.timer
mariana
```

SSH key authentication is the only remote entry point needed; no new public ports
are opened. Keep the server's standard firewall in place.

## Laptop connection profile

Create the gitignored `mariana-server.json` beside `MarianaBot-Server.cmd`:

```json
{
  "host": "YOUR_SERVER_PUBLIC_IP",
  "user": "ubuntu",
  "identity_file": "C:/Users/YOU/Documents/your-server-key.key"
}
```

Connect once with ordinary SSH to verify and accept the server's host key.
The shortcut uses that saved host key and refuses an unexpected replacement.
The connection profile contains the key's path, never its contents.

## Backups and maintenance

For a standalone Codex installation, install the daily CLI update timer:

```bash
cp deploy/marianabot-codex-update.service deploy/marianabot-codex-update.timer \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now marianabot-codex-update.timer
systemctl --user start marianabot-codex-update.service
```

The timer runs the official `codex update` command once a day. It holds MarianaBot's
launch and worker locks and defers if research is active or starting. It preserves
the configured models, account login and research files. Check its most recent
result with `journalctl --user -u marianabot-codex-update.service -n 20`.

The daily timer takes a consistent SQLite backup, including committed WAL data,
checks its integrity, and retains seven days in `~/.local/share/marianabot-backups`.
This database contains research, prompts, memory, messages and usage history.
These are local recovery copies: they do **not** protect against loss of the VPS
or its disk. Download copies periodically or configure separate private storage.
Client credentials, unsent UI drafts and `mariana.toml` are not included.

```bash
systemctl --user status marianabot-terminal
journalctl --user -u marianabot-terminal -n 50
systemctl --user list-timers marianabot-backup.timer
systemctl --user start marianabot-backup.service
mariana doctor
```

Application updates are checked automatically before connecting and at service
startup. They install only when no chat, Discord bot or research worker is using
the installation. To apply a pending update, `/pause`, wait for the worker to
finish, `/quit`, and reconnect. See [startup updates](updates.md).

For manual maintenance or changes to systemd unit definitions, `/pause` first,
stop `marianabot-terminal`, back up the database and configuration, pull the new
code and reinstall into `.venv`. Copy updated service files, reload systemd,
start the service and run doctor. Reopen chat and `/resume` when ready. An expired
provider login must be renewed using its official client.

Official client references:
[Codex installation](https://learn.chatgpt.com/docs/cli),
[Codex headless login](https://learn.chatgpt.com/docs/auth#login-on-headless-devices),
[Claude installation](https://code.claude.com/docs/en/setup),
[Claude login](https://code.claude.com/docs/en/authentication).
