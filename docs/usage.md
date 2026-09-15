# Usage reporting

Both provider rows remain above the editor while you read, scroll or write. ChatGPT
combines MB and RB; Claude shows JB. Counts belong to the active MarianaBot run. If
you open a different conversation while a worker is running, the strip names that
working run. Saved runs retain their totals after restart.

`in` means reported input tokens, including cached input; `out` means reported
output. Allowance percentages mean **used**. The two windows with highest reported
usage are shown, followed by the nearest reported reset or current cooldown and
snapshot age. Hover for cache details or additional windows; `/usage` lists the
full quota snapshot. Unknown values remain unknown; expired snapshots say refresh
due instead of pretending the allowance reset. Other apps affect account allowance
but do not contribute to MarianaBot's run token totals.

The UI reads persisted reports every 0.75 seconds. That is display refresh speed,
not a claim that providers supply continuous token counts:

- **Codex:** `exec --json` reports tokens in `turn.completed`. Active requests can
  use tokens before that report arrives. Account quotas refresh before dispatch
  and every 60 seconds while the worker runs through `account/rateLimits/read`.
  These account checks do not generate model tokens.
- **Claude:** `--include-partial-messages` exposes usage at message boundaries and
  cumulative `message_delta` updates when available. Assistant output placeholders
  are ignored; repeated blocks are deduplicated by message ID. A final `modelUsage`
  total takes precedence over partial reports and includes reported helper usage.
  Allowance information updates from `rate_limit_event`; the app does not poll an
  undocumented billing endpoint or send extra model prompts to obtain quotas.

Each invocation has one durable usage row. Final totals replace partial totals;
failed/cancelled attempts keep whatever they reported, and a retry counts as a new
attempt. Reusing a completed checkpoint adds no tokens. Unreported consumption
cannot be recovered by the counter. Exports include aggregate `reported_usage` in
`history.json`. Old completed responses are backfilled on opening existing stores.

No dollar estimate is presented as a subscription charge. Existing subscription
gates and your confirmed disabled overage settings continue to govern live work.

Protocol references:

- [Codex JSON events](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex account rate limits](https://learn.chatgpt.com/docs/app-server)
- [Claude streaming output](https://code.claude.com/docs/en/agent-sdk/streaming-output)
- [Claude usage accounting](https://code.claude.com/docs/en/agent-sdk/cost-tracking)
