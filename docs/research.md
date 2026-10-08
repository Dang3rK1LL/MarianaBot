# Research workflow and model routing

The objective is a useful answer to the original question using online evidence.
Reviews do not demand interviews, surveys, paid pilots or expert outreach.
Unavailable private facts remain limitations. Model agreement is not validation.

## Phases and feedback

Foundation establishes core facts, the target market and basic feasibility. JB must
set `foundation_ready` before validation starts. Validation checks the few unknowns
that could change the decision. Decision resolves contradictions and delivers a
recommendation. A changed brief revision restarts the foundation gate, retaining
saved evidence. Three independent RB proposals and three independent JB critiques
feed their respective chairs by default.

Specialists target 450 words, MB 250, and chairs at most 700.
`research.max_response_words` can lower those budgets. Overlong output is rejected
and retried within the configured limit. Reviews allow at most three blockers,
three online checks and two dissent notes. Approval rejects declared violations
of scope, owner constraints or online-only validation.

Ordinary chat messages on an open run are feedback. `/ask` is for questions.
Answered messages enter a verbatim owner ledger, separate from summarized memory.
Later explicit instructions supersede conflicting earlier ones; questions are
context, not automatically new constraints. MB acknowledgments cannot replace the
original brief or exact owner wording. Steering applies at a round boundary.

## Task routing

Models and effort levels come from authenticated subscription CLI catalogs.
Routing makes no additional model calls. Fable, API-key-only and reported
extra-usage or token-billing-only entries are excluded.

| Task | Adaptive selection |
|---|---|
| MB intake, owner reply, acknowledgment | Configured MB model, default Luna / low |
| Memory summary | MB / medium; available Sol after failed validation |
| Foundation and routine validation | Available Sol / medium for RB; Sonnet / medium for JB |
| Decision, persistent blockers, output-validation retry | Configured RB/JB model with effort up to high and the selected ceiling |

A lightweight RB/JB ceiling is retained rather than upgraded to a larger family.
Only reported effort levels are used. Fixed mode retains exact RB/JB selections;
MB stays separately configured. The sidebar distinguishes ceilings from actual
current/last selections. Each call saves model, effort, selection reason and
elapsed request time. MB has its own one-call scheduling slot so a long RB request
need not finish first; shared quota and request-spacing controls still apply.

## Validation and stopping

Regression tests cover phases, scope checks, owner-feedback retention across model
changes, independent teams, output budgets, billing exclusions, MB responsiveness
and invalid-summary recovery. Live JB reviews assess relevance, evidence and
constraint fidelity in the ordinary loop. Low scores and persistent blockers
trigger higher-effort work.

This is task-based routing, not a universal model ranking. Offline tests verify
orchestration, not the truth of future research or account-specific model quality.
No additional paid benchmark calls are made by the router.

Research completes on supported approval, an online-evidence plateau with no next
online check, or its round/time budget. Legacy `needs_human` does not pause the loop.
Owner controls, access failures and unrecoverable request errors still apply.
Elapsed limits include pauses and quota waits.

Primary design references:

- [OpenAI model selection](https://developers.openai.com/api/docs/guides/model-selection)
- [OpenAI reasoning effort](https://developers.openai.com/api/docs/guides/reasoning)
- [Anthropic routing and evaluator workflows](https://www.anthropic.com/engineering/building-effective-agents)
- [Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
