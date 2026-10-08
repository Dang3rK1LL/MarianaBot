# Use your subscriptions

MarianaBot's current integration uses **your personal official-client logins**.
It does not use OpenAI or Anthropic API keys, bill your API projects, or convert
ChatGPT web usage into API credit.

## What the providers document

OpenAI distinguishes ChatGPT subscription authentication from metered API-key
authentication in [Codex authentication](https://learn.chatgpt.com/docs/auth).
The official CLI's [non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
reuses saved client authentication.

Anthropic's [June 15 update](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
says the announced Agent SDK billing change was paused: Agent SDK and Claude's
print mode still draw from subscription usage. Some older documentation retains
the superseded announcement. This project follows the update for this personal,
local automation workflow. Recheck the linked guidance if billing rules change.

The [Claude headless guide](https://code.claude.com/docs/en/headless) documents
scripted requests. MarianaBot invokes that official client; it does not implement
a separate Claude OAuth login or proxy subscription credentials for other people.

## Local setup

1. Install the official Codex CLI and Claude Code for your operating system.
   Opus 5.5 requires Claude Code 2.1.280 or newer.
   Older versions may lack flags used by this integration.
2. Sign in through each client using your existing subscription:

~~~text
codex login
claude auth login --claudeai
~~~

3. Confirm the provider accounts have extra usage/usage-credit spending and
   automatic credit purchases disabled. Already purchased credits may also be
   consumed by the provider after included usage ends. Do not enable paid fallback.
4. Run `mariana setup`, choose models and efforts, and confirm the billing controls
   you checked. Setup records `subscription.overage_disabled = true` in
   `mariana.toml`. This is your attestation, **not a provider-setting toggle**.
5. Run `mariana doctor`. It checks client versions, login types, available Codex
   models and Codex account quotas without sending model prompts.
6. Open `mariana` and describe your problem.

API keys in the environment are removed for child clients. Claude safe mode avoids
custom API-key helpers; Codex explicitly requires a ChatGPT login. Fast mode and
explicit model fallback are not enabled.

## Models

MB defaults to `gpt-6-luna` / low. Adaptive routing uses Sol and Sonnet for routine
work; defaults for ceilings are `gpt-6-astra` at high effort and `claude-opus-5-5`
at medium effort for JB. Choose alternatives during `mariana setup` or `/models`.
See [Claude model configuration](https://code.claude.com/docs/en/model-config)
for the Opus 5.5 effort recommendation.
API availability does not itself establish subscription entitlement.

Doctor verifies the selected research model appears in your Codex model list. Claude authentication
metadata does not establish access to an individual model; that is checked during
the first real request. A model-access failure pauses the run. Fixed mode does not request model fallback; adaptive mode selects eligible
models from the subscription catalog. Fable and reported token-billing-only models
are excluded. See [research routing](research.md). Provider-side routing remains
controlled by the provider. Change the explicit model setting only if you intend that change.

## Quota monitoring

The [Codex app server](https://learn.chatgpt.com/docs/app-server) exposes
account/rateLimits/read with primary/secondary usage percentages and reset times.
MarianaBot checks it before each OpenAI dispatch, conservatively observes all
reported buckets, and shares the result across MB/RB.

Claude's stream can report rate_limit_event with utilization and resetsAt.
MarianaBot also uses the installed client's read-only `get_usage` control request
to read session, weekly and available model-specific allowance windows. Both
providers refresh at chat startup, cloud reconnect, every minute and through
`/usage`; research workers keep polling while the chat is closed. These checks
never send model prompts. Claude's usage protocol is experimental and may change
with client versions; missing or unreadable reports keep the last good snapshot.

The metadata process permits Claude's explicit usage lookup while keeping
telemetry, error reporting and auto-updates disabled. Research processes retain
their existing network restrictions. Claude may reuse a recent client cache;
MarianaBot preserves its observation timestamp and rejects stale cached fallbacks.
Stream events without utilization do not erase a measured percentage or refresh
its age. Unknown values are displayed as unknown.

MarianaBot waits when capacity is rejected or a relevant window reaches the
configured threshold. The default threshold is 95%; it cannot predict the
unknown usage of the next request. Model-specific windows apply to the selected
Claude model, rather than blocking it because a different model has exhausted
its own allowance.

If a rejection provides no usable reset time, the default retry delay is 30 minutes.
Cooldown timestamps survive restarts. The worker never buys credits, rotates
accounts, consumes earned-reset credits or switches to API billing.

Usage metadata, including Claude's API-equivalent dollar estimate when supplied,
is retained for diagnostics. That estimate is **not a subscription charge**.

## Scope of verification

See the [testing guide](testing.md#live-verification) for the scope and limits of
live verification. Model access and allowance depend on your own account.

Provider-side overage controls are necessary for the no-additional-spend intent.
The wrapper cannot inspect every billing switch, guarantee a request fits the
remaining quota, or prevent provider-side charges if those controls are enabled.
