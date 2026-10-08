# Research memory and compaction

Each request is a fresh client conversation with the original problem, owner
feedback, explicit pins, current work and rolling memory. It does not depend on
a particular model remembering a previous session.

## Owner contract

Original problems, current owner messages, answered feedback and explicit `/pin`
notes stay verbatim. MB acknowledgments cannot replace them. Ordinary chat messages
on an open run are feedback; `/ask` is for questions. Questions remain context,
not automatically constraints. Later explicit changes supersede conflicting
older instructions. Steering applies at round boundaries.

Generated briefs and plans can be summarized. Historical blockers, dissent and
requested experiments are not permanently pinned. The latest review supplies
active issues; every earlier review remains saved. Existing automatic pins are
retained as inactive records. Explicit owner pins remain active until `/unpin ID`,
which keeps their archived record.

The initial problem limit is 100,000 characters. Its original is preserved.
If the owner contract cannot fit the configured context, the worker pauses with
all text intact. Reconfigure the context bound or release obsolete explicit pins.
No finite context offers unlimited verbatim instructions or perfect recall.

## Working memory

Completed rounds and specialist findings enter rolling memory. Chair outputs
are included once through the saved round. Answered dialogue enters memory even
when responses finish out of order; exact owner wording also stays in the ledger.

`research.memory_chars` defaults to 8,000, capped at one quarter of the configured
context bound. Ordinary calls target at most 24,000 evidence characters, with
space up to `research.max_context_chars` when needed by the owner contract.
These are character budgets, not exact provider token-window meters. Concise
output budgets reduce growth before compaction becomes necessary.

Sources are archived before summarization. Content-based identities reuse saved
summaries across agents and restarts. Long sources use overlapping chunks. MB
preserves decision-critical facts, uncertainty, numbers, dates, source URLs,
contradictions and reasons for rejecting alternatives.

## Recovery

Invalid JSON, mismatched source IDs, blank summaries and invented URLs fail
validation. Bounded retries include correction instructions. Valid oversized
candidates can be shortened and reused after a restart. Rejected output stays
in private failed-call history and never becomes memory or exported citations.

If validation or size repair still fails, a bounded extract copies passages from
the original source. It is labeled as partial, with omissions available in the
full archive. Research can continue without treating it as exhaustive or verified.
Owner cancellation and terminal account failures still apply. Originals remain
intact; a fallback does not offer perfect semantic retention.

Compaction uses MB's efficient model and supported effort, escalating after failed
validation. It shares OpenAI allowance and the research scheduling gate. `/pause`
can cancel it, and completed work is reused after resume. No additional API billing
route is introduced.

`/export` includes `memory.md`, full rounds and dialogue, source/summary records,
model selections and active/released pins in `history.json`. Arbitrary archived
passages are not automatically retrieved by models. Pin exact information that
must remain immediately available. Compaction does not extend the run deadline.
