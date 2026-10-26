"""Live intake and round questions, with durable identifiers and novelty checks."""

import re
from difflib import SequenceMatcher
from typing import Annotated, Literal

from pydantic import Field, model_validator

from marianabot.config import StrictModel


class Deliverable(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    title: str = Field(min_length=3, max_length=120)
    acceptance: str = Field(min_length=3, max_length=500)
    kind: Literal["research", "creative", "calculation", "verification"] = "research"


class Intake(StrictModel):
    title: str = Field(min_length=3, max_length=60)
    objective: str = Field(min_length=5, max_length=300)
    brief: str = Field(min_length=20)
    deliverables: list[Deliverable] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def distinct_deliverables(self):
        if len({item.id for item in self.deliverables}) != len(self.deliverables):
            raise ValueError("Deliverable IDs must be unique")
        return self


class Question(StrictModel):
    topic: str = Field(pattern=r"^[a-z0-9_-]{3,60}$")
    question: str = Field(min_length=10, max_length=220)
    options: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        min_length=2, max_length=5
    )
    impact: str = Field(min_length=10, max_length=300)


class Questions(StrictModel):
    questions: list[Question] = Field(min_length=1, max_length=10)


class QuestionAudit(StrictModel):
    distinct_topics: bool
    new_information: bool
    easy_to_answer: bool
    relevant_to_round: bool
    corrections: list[str] = Field(default_factory=list)


QUESTION_AUDIT = """Audit the proposed round questions against the owner's original request,
all earlier questions, all answers and supplied owner facts. Reject semantic duplicates,
including paraphrases and differently named topic keys. Reject questions whose answers
are already supplied, generic repetitive priority questions, leading questions, compound
questions, research assignments or irrelevant new directions. Each must be easy to answer
and materially change this round. Return only JSON matching the supplied audit schema.
"""


def normalized(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold()))


def validate_questions(batch: dict, history: list[dict], maximum: int):
    proposed = batch["questions"]
    if len(proposed) > maximum:
        raise ValueError(f"Ask at most {maximum} questions, never more than ten")
    seen = list(history)
    for question in proposed:
        text = normalized(question["question"])
        if any(
            question["topic"] == old["topic"]
            or SequenceMatcher(None, text, normalized(old["question"])).ratio() >= 0.78
            or (
                len(set(text.split()) & set(normalized(old["question"]).split()))
                / max(1, len(set(text.split()) | set(normalized(old["question"]).split())))
                >= 0.65
            )
            for old in seen
        ):
            raise ValueError(
                "Questions must cover distinct new topics, not paraphrase earlier questions"
            )
        if len({normalized(option) for option in question["options"]}) != len(question["options"]):
            raise ValueError("Answer choices must be distinct")
        seen.append(question)


INTAKE = """MASTER_INTAKE: Read the owner's full request and return JSON matching the supplied schema.
Preserve the exact decision and all requested outputs in deliverables, with stable short IDs
and concrete acceptance criteria. Do not silently replace a brand/content/copy request with
only a business go/no-go memo. State impossible verification requirements explicitly; never
promise permanent handle/domain availability. Use a short title of 3–6 useful words, a
one-sentence objective, and a concise brief. The original owner text remains authoritative.
Classify copy, posts and design drafts as creative, source checks as verification,
and numerical scenarios as calculation. Creative drafts can be conditional, not validated.
"""

QUESTION_PROMPT = """ROUND_QUESTIONS: Return JSON matching the supplied schema. Ask a few short,
easily answered questions that will materially change THIS round's work on the owner's
original request. Ask no more than question_limit (absolute maximum ten). Use 2–5 short
choices, including an honest 'Not sure' choice when relevant; free-text answers are allowed.
Each question must cover a different decision or missing owner fact. Never repeat a topic,
paraphrase a prior question, ask for already supplied information, or ask two versions of
the same question. Read all prior questions AND their answers before proposing anything.
Use a stable semantic topic key, not a round number or a disguised duplicate. Ask about
owner facts, priorities or acceptable tradeoffs, not research the bot can perform online.
No interviews, outreach or testing assignments. State specifically how each answer changes
the next analysis or deliverable. Do not steer the owner toward an unsupported conclusion.
"""
