# Research memory

Each provider request includes the original owner request, the output contract,
exact owner feedback, active pins and current working state. Models do not depend
on remembering a previous conversation. Later explicit owner instructions override
conflicting older entries. Questions alone do not become constraints.

Guided questions and their exact answers are stored separately from summaries.
Their choices and intended effect accompany the answers. Both teams receive them;
the chair review records how the current round applied each answered question.
Owner text is never replaced by an MB acknowledgment or generated paraphrase.

The live JB chair supplies concise carry-forward notes as part of its ordinary
review. Completed reviews update rolling memory without a separate summarization
call. The latest plan and review remain available, and full specialist responses
stay archived. Oversized context uses labeled excerpts from actual saved text;
these excerpts are context preparation, not generated research answers.

`research.memory_chars` defaults to 8,000 and is bounded by the configured context.
`research.max_context_chars` defaults to 60,000. These are character budgets, not
provider token-window measurements. If the protected owner material cannot fit,
execution pauses with the original text intact. No finite context guarantees
perfect semantic retention. Pin exact facts that must remain immediately available.

`/export` includes working memory, full rounds, dialogue, guided answers, output
statuses, model records and source/excerpt records. Existing summary archives
remain readable; new runs do not call a standalone memory summarizer.
