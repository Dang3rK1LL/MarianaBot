# Testing

## Offline checks

From an activated development environment in the repository root:

```text
python -m pip install -e ".[dev,discord]"
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -m pytest -q
python -m marianabot demo --plain
```

The suite and demo use fixtures without provider logins or model calls. The demo
writes to its own data directory. Installing dependencies requires network access.
[CI](../.github/workflows/ci.yml) runs these checks on Windows and Ubuntu with
Python 3.11, 3.13 and 3.14. See [Actions](https://github.com/Dang3rK1LL/MarianaBot/actions)
for results tied to each commit.

Coverage includes research and review rounds, steering, cancellation, checkpoint
recovery, worker locking, quota waits, streamed usage accounting, protected memory,
compaction, model preferences, terminal interaction, exports and database backups.
Client protocol tests use fixture subprocesses; they cannot establish provider
behavior. Model-menu tests cover provider lists, changing effort options, unavailable
models, retry and cancellation. Startup-usage tests cover fresh limits on launch,
idle polling, cloud reconnects, manual refresh, retained snapshots after failure,
nonblocking editing and offline demos. Discord tests cover access control, command handling,
ordered stage updates, token snapshots, message limits and durable delivery.
Updater tests cover activation, concurrent processes, failed installs,
local edits and private-file preservation.

## Additional development tools

| Tool | Purpose | Requirements |
|---|---|---|
| [`scripts/capture_chat.py`](../scripts/capture_chat.py) | Capture the real terminal UI using synthetic research and usage | Base app; optional `resvg-py` for PNG output |
| [`scripts/preview_discord.py`](../scripts/preview_discord.py) | Preview notification cards with synthetic data as HTML and SVG | Base app; no bot connection or model calls |
| [`scripts/check_updater.py`](../scripts/check_updater.py) | Build, validate and launch an update in a disposable checkout | Git and network access for Python packages |
| [`scripts/check_linux_service.py`](../scripts/check_linux_service.py) | Check service interruption, checkpoint recovery and preservation of a deliberate pause | Linux, systemd user services and tmux |

Run a tool with `python scripts/NAME.py` from the repository root. None of these
tools calls a model provider. The updater check uses temporary repositories and
environments. The Linux check creates and removes a temporary user service and
demo database, independently of the normal research service.

The screenshot tool writes to the gitignored `.mariana/visual-review/` directory.
The published images use synthetic data: `usage.png` supplies
[`docs/assets/chat.png`](assets/chat.png), and `models-narrow.png` supplies
[`docs/assets/models.png`](assets/models.png). Review any replacement before
publishing it; personal research should never appear in documentation screenshots.

## Live verification

`mariana doctor` checks installed client versions, authentication metadata and
available Codex models without sending model prompts. Use the separate
[live connectivity check](operations.md#explicit-live-connectivity-test) only
when you intend to consume subscription allowance. Its records remain private
under `.mariana/`; CI and normal startup never invoke it.

Short Astra and Opus 5.5 connectivity requests have verified access and response
parsing through the official clients. The staged installer, offline demo and
systemd recovery checks have also passed on ARM64 Ubuntu. These checks do not
establish multi-day research quality, native search and citation quality,
real-model compaction fidelity, or a real subscription-exhaustion/reset cycle.
The optional Discord integration has offline coverage; a real bot connection
requires each user's own setup.

To evaluate research quality, compare the same problem with a single research
pass and with several MarianaBot rounds. Review factual accuracy, evidence,
economic realism, executable actions and testable assumptions without identifying
which method produced each result. Track supported findings and resolved blockers;
a higher judge score alone is not evidence of a better business plan.
