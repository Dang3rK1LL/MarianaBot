# Research workflow

Research answers the original assignment using public online evidence. Interviews,
outreach, surveys, paid pilots and in-person testing are excluded. Unavailable
private facts stay explicit limitations; model agreement is not validation.

## Intake and modes

Before creation, choose a work folder and Automatic or Guided mode. Live MB intake
records a short title, objective and every requested deliverable with a stable ID
and acceptance criteria. This contract prevents a decision memo from silently
replacing requested brand, content or copy outputs. Conditional creative drafts
remain possible even when commercial validation is incomplete.

Automatic mode researches independently. Guided mode asks up to the selected
question count before each round (default three, maximum ten). Questions address
missing owner facts or decisions that change the upcoming work. They must be
short, easily answered, mutually distinct and different from earlier rounds.
A round may ask zero questions when existing answers and online evidence are
sufficient; the independent audit must approve that choice before work continues.
A live independent audit checks relevance, novelty and already-known information.
Rejected batches are regenerated within the configured retry limit. Resuming after
exhausted retries makes fresh attempts with the last audit's feedback; it does not
replay the same rejected batches. Changed owner context invalidates unused batches.

Questions and answers persist across restarts. Answer all pending questions in the
app, with `/answer QUESTION_ID text`, or through Discord. No answer is selected
silently. The final saved answer continues an unpaused run; a deliberate owner
pause remains paused. Time spent answering completed question batches extends
the execution deadline. No other research is interrupted to make room.

Both RB and JB receive exact answers in protected owner context. Reviews must
explain how current answers affected the plan, assumptions or deliverables.
Acknowledgments alone do not satisfy this check.

## Phases and routing

Foundation establishes feasibility before detail. Online validation targets the
few unknowns that could change the decision. Decision resolves contradictions and
completes the requested outputs. Three independent RB proposals and three JB
critiques feed their respective chairs by default. Calls run sequentially unless
concurrency is configured.

Authenticated CLI catalogs supply available models and efforts. Adaptive routing
uses the lightweight MB selection for intake and replies, Sol/Sonnet for routine
research/review and the configured ceilings for difficult decisions. Question
audits use available Sonnet within the JB effort ceiling. Fixed mode retains the
chosen RB/JB models. Fable and reported token-billing-only models are excluded.
Memory updates reuse the live review; they make no extra model calls.

Specialists target 450 words, MB 250 and chairs 700. Length is a writing preference;
full provider text is retained. Invalid structured outputs require real corrected
provider responses, bounded by the retry limit. Errors never produce fabricated
research. Exact owner instructions, requested outputs and online-only scope are
checked before approval.

## Stopping

New runs default to eight rounds, a minimum of two and 72 elapsed hours. Existing
runs retain their saved settings. Two consecutive decision-ready reviews with
the same decision, no material change and no unresolved blockers can finish with
explicit evidence limitations. This avoids chasing a score after the useful
conclusion stabilizes. Each requested output needs a status; completed items need
a quotation present in the plan. Creative outputs cannot simply be declared
impossible because commercial evidence is missing. Partial requested outputs block
all early completion paths, including approval and evidence plateaus. Unverifiable
external facts may remain explicit limitations; creative drafts must be supplied.

Supported approval, exhausted useful online checks and round/time limits remain
stop conditions. Research can still pause for owner controls, access failures,
invalid output after bounded retries or a protected context that cannot fit.
Completing research means the bounded assignment ended, not that a business is
proven viable. Full outputs and limitations remain available for review.

Primary design references:

- [OpenAI agent evaluations](https://developers.openai.com/api/docs/guides/agent-evals)
- [Anthropic evaluator workflows](https://www.anthropic.com/engineering/building-effective-agents)
- [Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
