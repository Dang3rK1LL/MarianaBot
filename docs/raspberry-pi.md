# Raspberry Pi deployment

Use a 64-bit Raspberry Pi OS or compatible Debian installation, Python 3.11+,
stable internet access, and sufficient storage for client installations and history.
The models run remotely. Official Claude Code specifies ARM64/x64 and 4 GB+ RAM
in its [setup requirements](https://code.claude.com/docs/en/setup).
Keep concurrency at one per provider initially; MB can overlap with JB.

This service template is prepared for deployment. It has not been tested on the
owner's Pi. Complete an interactive run and interruption/resume checks on that
device before leaving a long-running job unattended.

## Install as your normal user

~~~bash
git clone https://github.com/Dang3rK1LL/MarianaBot.git ~/MarianaBot
cd ~/MarianaBot
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/mariana demo --plain
~~~

Install ARM64 builds of the official clients using their current instructions:

- [Codex installation](https://github.com/openai/codex)
- [Claude Code installation](https://code.claude.com/docs/en/setup)

Log into both clients as the same Linux user that will run the service.
For Codex on a headless device, use its documented device-login flow if available;
check codex login --help. Keep authentication managed by the official clients.
Do not commit login files or embed credentials in the service.

~~~bash
codex login
claude auth login
.venv/bin/mariana init --data-dir ~/.local/share/marianabot
.venv/bin/mariana doctor --data-dir ~/.local/share/marianabot
~~~

Review billing controls and configuration in [subscription setup](subscriptions.md).
If the clients are installed through a version manager, set absolute executable
paths in mariana.toml because systemd does not load your interactive shell profile.

Create a real problem only when ready to use the subscription allowance:

~~~bash
.venv/bin/mariana new --problem-file examples/problem.md --data-dir ~/.local/share/marianabot
.venv/bin/mariana run RUN_ID --data-dir ~/.local/share/marianabot
~~~

Check MB's brief, interrupt with Ctrl+C, and verify a resume reuses completed
checkpoints. Complete a modest run before enabling the service.

## Run without keeping SSH open

The template assumes the repository is at ~/MarianaBot and state is at
~/.local/share/marianabot. Adjust the unit if you use different paths.

~~~bash
mkdir -p ~/.config/systemd/user
cp deploy/marianabot@.service ~/.config/systemd/user/
systemctl --user daemon-reload
loginctl enable-linger "$USER"
systemctl --user enable --now marianabot@RUN_ID.service
~~~

The linger command may require administrator authorization on your distribution.
It keeps your user service manager available after logout. The service uses the
same saved client logins as your interactive account.

Normal completion stops the service. Setup errors and deliberate pauses are not
automatically restarted. Unexpected failures restart after 30 seconds, with a
restart-frequency limit. SIGTERM checkpoints local work before shutdown; the unit
also kills remaining child processes in its control group.

~~~bash
systemctl --user status marianabot@RUN_ID.service
journalctl --user -u marianabot@RUN_ID.service -f
.venv/bin/mariana watch RUN_ID --data-dir ~/.local/share/marianabot
.venv/bin/mariana steer RUN_ID "New customer evidence..." --data-dir ~/.local/share/marianabot
~~~

After a deliberate pause, start the service explicitly to resume:

~~~bash
systemctl --user start marianabot@RUN_ID.service
~~~

## Updating and recovery

Stop the service before updating source or clients. Back up state, pull the new
code, reinstall the package, run doctor and the offline demo, then restart.
If the unit reaches its restart limit, investigate the journal before using
systemctl --user reset-failed.

Use a reliable power supply and storage. SQLite protects transactional checkpoints,
but backups are still necessary. No network port or public web interface is opened.
Use SSH to operate the CLI on the home network.
