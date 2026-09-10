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
   Development account checks used Codex 0.154.0 and Claude Code 2.1.257.
   Older versions may lack flags used by this integration.
2. Sign in through each client using your existing subscription:

~~~text
codex login
claude auth login
~~~

3. Confirm the provider accounts have extra usage/usage-credit spending and
   automatic credit purchases disabled. Already purchased credits may also be
   consumed by the provider after included usage ends. Do not enable paid fallback.
4. Run mariana init, then mariana doctor. Doctor checks client versions, login
   types, available Codex models and Codex account quotas. It sends no model prompts.
5. After checking the billing controls, set subscription.overage_disabled = true
   in mariana.toml. This is your attestation, **not a provider-setting toggle**.
6. Create a problem and run it.

API keys in the environment are removed for child clients. Claude safe mode avoids
custom API-key helpers; Codex explicitly requires a ChatGPT login. Fast mode and
explicit model fallback are not enabled.

## Models

MB and RB request gpt-6-astra. JB requests claude-opus-5.
API availability does not itself establish subscription entitlement.

Doctor verifies Astra appears in your Codex model list. Claude authentication
metadata does not establish access to an individual model; that is checked during
the first real request. A model-access failure pauses the run. There is no silent
substitution. Change the explicit model setting only if you intend that change.

## Quota monitoring

The [Codex app server](https://learn.chatgpt.com/docs/app-server) exposes
account/rateLimits/read with primary/secondary usage percentages and reset times.
MarianaBot checks it before each OpenAI dispatch, conservatively observes all
reported buckets, and shares the result across MB/RB.

Claude's stream can report rate_limit_event with utilization and resetsAt.
MarianaBot keeps those observations and waits when capacity is rejected or reaches
the configured threshold. The default threshold is 95%; it cannot predict the
unknown usage of the next request. Claude does not always provide a complete
account snapshot through this stream. Unknown values are displayed as unknown.

If a rejection provides no usable reset time, the default retry delay is 30 minutes.
Cooldown timestamps survive restarts. The worker never buys credits, rotates
accounts, consumes earned-reset credits or switches to API billing.

Usage metadata, including Claude's API-equivalent dollar estimate when supplied,
is retained for diagnostics. That estimate is **not a subscription charge**.

## Scope of verification

Both installed client logins were checked without inference. Codex listed Astra
and Claude reported a Pro plan. The owner explicitly requested no live model
tests during development, so Opus access, generated research quality, native
web-search behavior and a real exhaustion/reset cycle remain unverified.

Provider-side overage controls are necessary for the no-additional-spend intent.
The wrapper cannot inspect every billing switch, guarantee a request fits the
remaining quota, or prevent provider-side charges if those controls are enabled.
