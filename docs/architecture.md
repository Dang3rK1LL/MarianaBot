# Architecture and cost policy

Python 3.11+, asyncio, HTTPX, SQLite WAL, and a Rich/Typer terminal interface.
Models run in provider datacenters; the Raspberry Pi only orchestrates requests.
One worker owns a data directory through an OS file lock. Other CLI processes can
read status and submit commands through SQLite. Separate data directories do not
share budgets; use a single directory for all personal runs.

## Round protocol

1. MB prepares a problem brief with constraints, assumptions, questions and success criteria.
2. Independent RB specialists propose evidence-backed approaches.
3. An RB synthesizer compares proposals, selects an approach, preserves dissent,
   and writes the current business/action plan.
4. Independent JB specialists attack evidence, economics and execution risks.
5. A JB chair compares critiques and produces structured findings, a score,
   blocking issues, a verdict, and the next RB prompt.
6. Pending user questions/steering go to MB at the next round boundary. Steering
   updates the brief and resets convergence tracking without deleting history.

Every call has a stable task identity, immutable output, model ID, token counts,
source metadata, and a conservative charge. Completed outputs are reused on resume.
Prompts use a bounded current brief, previous plan/review and specialist outputs;
full history remains on disk for inspection/export. Model text is untrusted data,
and cannot execute shell commands or change budgets or credentials.

## Budget reservations

Daily budgets apply globally to this data directory, in UTC; per-run budgets apply
for the lifetime of a run. MB and RB both count as OpenAI. Reservations and settled
charges count toward both caps. Daily exhaustion waits until midnight UTC; a request
larger than the whole daily cap or remaining run budget pauses for intervention.

Text-only input is conservatively bounded by UTF-8 bytes plus envelope allowance.
Native OpenAI web search adds hidden context. Search calls therefore reserve the
configured model's entire context window as input plus the maximum output and tool
fees. This can require more than $27 of *available headroom per concurrent search*
with the default price ceilings, even when the settled call is much cheaper.
Lower concurrency reduces simultaneous reservations. Disabling web search removes
that headroom requirement but also removes live retrieval; outputs are labeled accordingly.

Prices are explicit conservative ceilings, not a live invoice feed. Astra's defaults
cover long-context rates and cache writes; Anthropic prompt caching is not requested.
Output tokens include reasoning/thinking. Actual counts settle the reservation at
the configured ceiling prices. Unexpected usage above a reservation pauses the run.
Unknown outcomes retain the full reservation indefinitely. Retrying may incur another
charge and requires a new reservation. Budget bookkeeping is deliberately conservative.

Do not run other clients against the same spending allowance and assume this ledger
sees them. Provider account controls remain the authority on account-wide spending.

## Limits and interruption

Shared provider gates pace requests locally, observe request/token reset headers,
and account for in-flight reservations before dispatching more agents. HTTP 429s
with quota/spending errors pause; temporary 429/5xx errors use bounded backoff and
Retry-After. Header resets and cooldowns persist across worker restarts.

SIGINT/SIGTERM, CLI pause/stop and elapsed-time limits cancel local work, preserving
completed calls. A provider can finish an already-dispatched request after cancellation;
its reserved charge is retained. One cannot promise exactly-once paid execution across
a network failure without a provider-supported idempotency/retrieval contract.
