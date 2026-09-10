# Architecture

Python 3.11+, asyncio, SQLite WAL, official Codex/Claude Code clients, Rich and Typer.
The Pi orchestrates cloud models; it does not host model weights.

## Components

| Component | Responsibility |
|---|---|
| MB / Astra | Initial brief, questions about progress, owner steering |
| RB specialists / Astra | Independent proposals covering market, economics and execution |
| RB chair / Astra | Compare proposals, preserve dissent, synthesize the plan |
| JB critics / Opus 5 | Independent attacks on evidence, economics and failure modes |
| JB chair / Opus 5 | Compare critiques, emit validated review JSON and the next RB prompt |
| Engine | Scheduling, checkpoints, stop conditions and controls |
| Subscription limits | Shared MB/RB usage gate, independent JB gate, durable reset timestamps |
| Store | Runs, prompts/responses, rounds, commands, events and limits |
| CLI | Dashboard, owner mailbox, pause/resume/stop and exports |

Subagents are separate official-client invocations with distinct role prompts and
contexts. The Python scheduler controls their count and concurrency; no hidden
recursive model delegation is required. Chairs receive all specialist contributions
within the configured context bound and explain the comparison.

## Protocol and memory

1. MB converts the owner's problem into a research brief.
2. RB specialists work independently on that brief and the last completed plan/review.
3. RB compares their recommendations and writes a standalone plan.
4. JB critics independently inspect the plan, optionally searching for contrary evidence.
5. JB compares critiques and returns a schema-validated review.
6. The plan, review and round number commit together in one SQLite transaction.
7. Owner steering is handled by MB before the next round starts.

Review JSON contains score, verdict, strengths, blocking issues, next_prompt,
human_tests and dissent. Malformed output cannot advance a round. Validation errors
and temporary client failures have a bounded retry count; subscription exhaustion
waits for reset independently of that retry count.

Prompts allocate a bounded share of context to each component and visibly mark
truncation. Full prompts and responses remain on disk. This bounded working memory
is intentionally simpler than retrieval over an unlimited transcript; very long
specialist outputs can be clipped before synthesis.

A separate MB mailbox task answers owner questions during research. It shares the
RB provider semaphore, so questions wait if OpenAI capacity is occupied or exhausted.
Steering is applied only at round boundaries to avoid changing an in-flight round.

## Persistence and recovery

One process holds worker.lock for the entire data directory. Other processes can
read snapshots and enqueue controls using SQLite. Use the same data directory
everywhere; independent directories do not coordinate workers or quotas.

Each task has a stable identity incorporating round and brief revision. A completed
response is cached before subsequent tasks start, and re-used after a restart.
In-flight calls interrupted by cancellation or power loss are marked unknown.
The provider may already have consumed usage, so exactly-once inference is not
guaranteed across network/power failures. Unknown calls can be retried on resume.

SIGINT, SIGTERM and owner controls cancel child work. Linux process groups and
Windows process-tree termination keep local child clients from running detached.
A remote provider may still finish an already-dispatched request.

Each run stores a configuration snapshot. To change a paused run explicitly, use
resume --config with a validated configuration. The elapsed-time deadline starts
when the run is created and includes pauses and quota waits. Expired/finished runs
are not automatically restarted; create a new run from the exported plan.

## Stopping conditions

Sustained approval requires a configured number of qualifying reviews, a minimum
number of rounds under the current brief revision, sufficiently high scores, and
no blocking issues. A score plateau or needs_human verdict pauses for owner input.
Round/time exhaustion marks the run complete with the specific reason, regardless
of quality. Brief steering starts a new convergence history without erasing old work.

## Authentication and permissions

Only official clients contact the model services. MarianaBot never reads, copies,
exports or embeds login tokens. It checks login metadata through documented client
interfaces. Child environments remove API-key and third-party billing overrides.

Codex executes with explicit model, subscription-only login, read-only sandbox,
ignored user config, and disabled shell, apps, plugins, hooks and built-in delegation.
Claude uses safe/restricted mode, disabled customizations, empty MCP configuration,
explicit model, and an empty tool list or WebSearch only. Fast mode is disabled.
Model output is never passed to a shell.

These controls reduce the clients' capabilities; they are not an independent OS
security boundary against a compromised native client. Use a dedicated OS account
and keep official clients updated. All prompts sent for research go to the respective
provider; web search can transmit search queries.

## Evidence quality

Live search is available to both specialist teams by default. Chairs use supplied
evidence. When retrieval is disabled, prompts say so. Exported citations are model
claims about sources, not independently verified provenance. Human tests, dissent,
and unresolved assumptions remain in the final report.
