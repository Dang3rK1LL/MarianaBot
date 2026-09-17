# Research memory and compaction

Each model request starts a fresh conversation. MarianaBot supplies the current
brief, relevant current work, a working research memory and protected notes. It
does not rely on either CLI retaining a multi-day conversation behind the scenes.

After completed rounds, memory incorporates the plan, review and all completed
specialist/chair outputs. Answered owner messages enter the same memory, including
when questions and steering finish out of order. Short material stays unchanged.
MB summarizes larger material, prioritizing evidence, decisions and their reasons,
numbers and dates, source URLs, uncertainty, rejected options and open questions.
The prompt explicitly preserves contrary findings rather than favoring the plan.

The working memory targets one quarter of `research.max_context_chars` (15,000
characters by default). Oversized inputs to an individual research or synthesis
request are also compacted. Long sources are processed in overlapping chunks and
merged, so the compactor receives the whole source instead of only its beginning.
This is a character budget for evidence, not an exact provider token-window meter;
client instructions, tools and reasoning have their own overhead.

## What remains verbatim

- The current research brief and the current owner message.
- Explicit `/pin` instructions.
- Recorded review blockers, dissent and requested human tests, deduplicated by
  exact text and category. These are historical concerns to assess against current
  evidence, not a claim that every past objection is still valid.

The first problem reaches MB intake in full within the supported 100,000-character
input limit. Later requests can summarize that original problem; the current brief
stays intact. Use `/pin` for critical original wording that must survive even if MB's
brief failed to capture it. Conflicting owner instructions should be surfaced.

The model cannot remove protected notes. `/memory` shows note IDs; `/unpin ID`
releases one from future verbatim context, keeping its archived record. A released
note is not automatically reactivated by loading older reviews. `/pin` records an
instruction; use `/steer` as well when changing the research direction.

If protected text consumes the available context, the run pauses with guidance to
review obsolete pins or increase the context budget when explicitly reconfiguring
the paused run. It does not silently shorten protected text.

## Durability and usage

Original text is archived before compaction. Summaries have stable content-based
identities and are reused across parallel agents and restarts. The memory checkpoint
advances only after the necessary summary succeeds. Invalid JSON, mismatched source
IDs, oversized/empty summaries and newly invented source URLs fail validation;
bounded retries and pause behavior use the ordinary worker controls.

Compaction uses the selected MB/RB model and effort, shares their concurrency and
quota gate, and contributes to the ChatGPT usage row. `/pause` can cancel it. There
is no additional provider account or API billing route. Already completed research
and summaries are reused after resume. New worker processes use compaction on
existing runs too, rebuilding memory from saved checkpoints as needed.

`/export` includes `memory.md` plus memory, pins (including released ones), full
compaction source/summary records and original research in `history.json`.

Semantic summaries can lose nuance or make mistakes. Structural validation and
verbatim protection improve retention; they do not prove factual correctness or
perfect recall. Original archives remain available for inspection, but models do
not automatically retrieve any arbitrary archived passage. Pin information that
must remain immediately available to every research step.

Compaction does not extend the research deadline. Defaults remain 24 rounds and
72 elapsed hours; a week-long run requires appropriate time/round settings and
can still pause earlier for human evidence or a plateau.
