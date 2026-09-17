# CLI operations

For everyday use, run `mariana` or `mariana chat` to open interactive chat. On
Windows, double-click `MarianaBot.cmd`. The [laptop guide](laptop.md) covers its
slash commands. The commands below are the scriptable interface.

Activate the virtual environment or use its full executable path. All commands
accept --data-dir; always use the same path for the worker and control terminals.

## Preparing a useful problem

A good initial brief names the decision, customers/geography, resources, constraints,
time horizon and evidence already available. See examples/problem.md.
The initial problem is limited to 100,000 characters. MB identifies missing
information and working assumptions. Read its response with messages, then send
clarifications with steer.

~~~text
mariana new --problem-file examples/problem.md
mariana run RUN_ID
~~~

Creating a run stores the problem and configuration without inference.
Running it starts MB, then the research/review teams. The default limit is 24
rounds or 72 elapsed hours, including quota waits.

## Watching and steering

~~~text
mariana status
mariana status RUN_ID
mariana watch RUN_ID
mariana ask RUN_ID "Which objections have been resolved with evidence?"
mariana messages RUN_ID
mariana steer RUN_ID "I interviewed five customers; here are their responses..."
~~~

Ask uses an available MB slot while the worker runs. MB shares OpenAI usage with
research agents, so it also waits during an OpenAI cooldown. Steering is a queued
brief revision applied before the next round. Questions and responses are durable
and included in exported reports.

The chat interface starts an MB-only worker for questions on paused or finished
runs. Shell `ask` only queues a question; open that session in chat and use
`/retry` if no research worker is active.
A pause for human evidence or a plateau requires new steering to make progress;
simply resuming the unchanged brief reaches the same stopping condition.

## Stopping and restarting

~~~text
mariana pause RUN_ID
mariana resume RUN_ID
mariana resume RUN_ID --config mariana.toml
mariana stop RUN_ID
~~~

Ctrl+C and SIGTERM pause a foreground `mariana run` worker. In interactive chat,
use `/pause`; closing chat detaches and leaves its managed worker running.
Stop permanently closes the research run.
Completed or stopped runs remain exportable; start a new run from the plan to
continue. Explicit reconfiguration keeps the original creation time, historical
model records and completed checkpoints.

One worker can own a data directory. A second worker exits with a clear message;
it cannot reset the first worker's control state or duplicate its calls.

## Exports and backups

~~~text
mariana export RUN_ID
mariana export RUN_ID --output .mariana/my-report
~~~

The worker exports automatically when it exits normally or pauses.
Export while running to inspect the last completed round. Full prompts and model
outputs, including intermediate work, are in history.json. Citation URLs are
leads to inspect, not independently verified facts.

Exports also include `memory.md`. `history.json` retains original compaction
sources, summaries and released pins. Compaction pauses on validation failure or
protected-context overflow; it never silently clips protected notes. Inspect
`/memory` in chat and release obsolete notes with `/unpin ID`, or explicitly
resume with a larger `research.max_context_chars` configuration where appropriate.

For a simple consistent backup, stop/pause the worker and copy the entire data
directory. If backing up while it runs, use SQLite's backup API rather than
copying just the .sqlite3 file: WAL files can contain recent commits.
Treat backups as confidential and keep official-client credentials separate.

## Troubleshooting

| Symptom | Action |
|---|---|
| Configuration missing | Run init, then edit the local file. |
| Live run pauses before any call | Check subscription login and the overage-disabled attestation. |
| Model unavailable | Confirm your plan's access; change the model only intentionally. |
| Waiting at 95% | The configured threshold protects headroom; inspect the displayed reset. |
| Claude usage unknown | No complete quota event has arrived; a limit rejection still schedules a wait. |
| Pauses on billing/credit error | Review account settings; this is not treated as a temporary quota reset. |
| Client fails after an update | Run doctor and compare versions with docs/validation.md. |
| Invalid judge JSON | Completed specialists remain cached; resume permits a fresh chair attempt. |
| Human-evidence pause | Queue the new evidence with steer, then resume. |
| Completed due to time limit | Create a new run from the exported plan. |
| Service cannot find a CLI | Set full codex_command/claude_command paths before creating the run. |

CLI exit codes: 0 for normal completion/closed run, 2 for setup or command
errors, 3 for a deliberate worker pause. Unexpected crashes remain nonzero so the
service can restart from checkpoints. Startup diagnostics redact common credential
formats; raw client logs are not saved.

## Explicit live connectivity test

The smoke script is never run by CI or normal startup. It requires explicit
credit-use acknowledgement, uses low effort, disables search, asks for a tiny
JSON response and makes one invocation per selected provider:

~~~text
python scripts/live_smoke.py --allow-credit-usage
python scripts/live_smoke.py --allow-credit-usage --provider openai
~~~

Claude is given a $0.10 session budget, a 256-token output setting and one agentic
turn. These are client controls, not a guarantee of a provider-side invoice cap.
The script itself does not retry; native clients may handle transport retries.
Smoke records stay private under .mariana. Passing confirms basic connectivity
and response parsing, not a full multi-day research workload.
