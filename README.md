# MarianaBot

Take a business problem below the surface: a master coordinator, a research team,
and an independent adversarial review team, running persistently on your own hardware.

```text
                         YOU
                          |
                   MB · GPT-6 Astra
                  brief / questions / steering
                          |
   RB · GPT-6 Astra       |        JB · Claude Opus 5
   parallel researchers --+-----> parallel critics
   compare + synthesize <------- compare + next challenge
                          |
                evidence, plans, checkpoints
```

MB and RB share the OpenAI key, rate limiter, and spending ledger. JB uses Anthropic.
No paid calls are enabled by default. The application requires explicit daily and
per-run budgets. Credentials and research data stay out of Git.

## Development milestones

1. Foundation: architecture, validated configuration, packaging, documented cost policy.
2. Engine: persistent orchestration, all three brains, budget reservations, provider limits.
3. Product: CLI, offline demonstration, resilience tests, CI, Raspberry Pi service and operations docs.

Each milestone is committed and pushed to this repository.

## Design commitments

- Persist each completed agent call; resume without repeating completed work.
- Reserve costs transactionally before dispatch, including concurrent work.
- Keep ambiguous requests charged conservatively after timeouts or power loss.
- Follow provider reset headers and distinguish rate limits from billing failures.
- Preserve minority objections, source provenance, uncertainty, and human validation tasks.
- Stop on configured round/time/spending limits, sustained approval, or a score plateau.
- Keep pause, stop, credentials, and budget controls outside model authority.

Rate limits control throughput; they are not spending allowances. Local accounting
cannot see usage by other applications, changes in prices, or delayed provider invoices.
Use dedicated API projects/workspaces and provider spending controls as well.

Repeated model critique is a method to test, not a guarantee of business quality.
Plans must retain unknowns and identify customer interviews, experiments, and other
real-world evidence that would confirm or disprove the recommendation.

## Official integration references

Verified 2026-09-10:

- [GPT-6 Astra model](https://developers.openai.com/api/docs/models/gpt-6-astra)
- [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits)
- [OpenAI web search](https://developers.openai.com/api/docs/guides/tools-web-search)
- [Claude Opus 5](https://platform.claude.com/docs/en/models/opus-5/whats-new-opus-5)
- [Anthropic rate limits](https://platform.claude.com/docs/en/api/rate-limits)
- [Anthropic streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
