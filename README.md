# MarianaBot

**Take a business problem below the surface.**

MarianaBot is a personal CLI that runs a persistent conversation between a research
team and an independent critical review team, with a master coordinator for you.

~~~text
                         YOU
                          |
                   MB · GPT-6 Astra
                brief / questions / steering
                          |
   RB · GPT-6 Astra       |        JB · Claude Opus 5
   independent proposals -+-----> independent critiques
   compare + synthesize <------- compare + next challenge
                          |
               plans, evidence, checkpoints
~~~

**Subscription mode:** MB and RB use the official Codex CLI with your ChatGPT login.
JB uses the official Claude Code CLI with your Claude subscription login.
MarianaBot never calls the paid APIs directly and removes API-key overrides from
client environments. Model access still depends on your account.

## Try it without using any subscription allowance

Python 3.11 or newer:

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m marianabot demo
~~~

On Linux/macOS:

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/mariana demo
~~~

The demo uses deterministic fixtures, performs two full research/review rounds, and
exports a Markdown report and JSON history under .mariana/demo/exports/.
It makes **no network or model calls**. Use --plain for a scrolling log.

## Set up a real run

Read [subscription setup](docs/subscriptions.md) first. API keys do not draw from
Plus or Pro subscriptions. Extra usage and automatic credit purchases must be off
in the provider accounts before enabling a live run.

~~~text
mariana init
mariana doctor
mariana new --problem-file examples/problem.md
mariana run RUN_ID
~~~

The first command creates a local mariana.toml. Set subscription.overage_disabled
to true only after checking the provider billing settings yourself.
The configuration defaults to false; live inference is blocked until it is set.

MB turns the initial problem into a brief with constraints, assumptions, success
criteria and questions. Three RB specialists independently work on the brief, then
an RB chair compares their proposals. Three JB critics independently challenge the
plan, then a JB chair compares objections and produces the next research prompt.

Specialist count and concurrency are separate settings. Defaults are three
specialists per brain, scheduled one at a time to conserve subscription headroom
and Pi memory. Increase concurrency to run multiple specialists simultaneously.
MB and RB always share the same OpenAI gate.

## Stay in control

Use a second terminal, with the same data directory:

~~~text
mariana watch RUN_ID
mariana ask RUN_ID "What is the weakest assumption so far?"
mariana messages RUN_ID
mariana steer RUN_ID "Focus on Hungary and require a pilot below EUR 500."
mariana pause RUN_ID
mariana resume RUN_ID
mariana stop RUN_ID
mariana export RUN_ID
~~~

Questions go to MB while research runs. Steering updates the brief at the next
round boundary and resets convergence tracking. Pause and stop are local controls
that do not need a model response. Ctrl+C pauses and preserves checkpoints.

## What is persisted

Every completed agent response, its input prompt, model, usage metadata, source
metadata, each round's plan and review, MB conversation, and observed quota resets.
A single-worker lock prevents duplicate workers in one data directory.
SQLite checkpoints let a restarted process skip completed agent tasks.

Exports include:

- report.md: current brief, latest completed plan, critical review and owner conversation.
- history.json: full local run record, completed prompts and responses.
- citations.md: model-cited URLs, explicitly labeled as unverified.

Research data and logins are not pushed to GitHub. Local reports can contain your
confidential business information; keep the data directory private.

## Usage limits and practical boundaries

Codex account usage is checked before each MB/RB dispatch. Claude's reported
usage/reset events are recorded as they arrive. The worker waits at the configured
usage threshold or after a limit rejection, using a reported reset when available.
If Claude supplies no reset, it waits a conservative interval before retrying.
Unknown usage is shown as unknown, never as a fabricated balance.

A provider may count a request before the client reports its outcome. Interrupted
requests are recorded as unknown and may consume usage again if retried.
Other apps share your subscription allowance. No local wrapper can promise exact
remaining capacity or prevent charges if paid overage is enabled in your account.

Rounds stop at configured time/round limits, sustained qualified approval, a score
plateau, or a request for human evidence. A higher reviewer score is **not proof**
of a stronger business. The prompts preserve dissent, uncertainties and experiments
that could disprove the recommendation.

## Documentation and verification

- [Subscription authentication and billing](docs/subscriptions.md)
- [Architecture and recovery](docs/architecture.md)
- [CLI operations](docs/operations.md)
- [Raspberry Pi deployment](docs/raspberry-pi.md)
- [Validation and milestones](docs/validation.md)

Live model testing is intentionally disabled for this development session at the
owner's request. Offline tests exercise orchestration, interruption/resume, mailbox
handling, stopping conditions, quota events, and subprocess protocol contracts.
Account checks confirmed ChatGPT authentication and Astra availability in Codex,
plus a Claude Pro login. Opus 5 inference and Raspberry Pi deployment remain to be
verified on the target accounts and device.
