"""Research notification cards, built from saved output without additional inference."""

import json
import re

from marianabot.presentation import STAGES, research_brief, section, sentence
from marianabot.usage_ui import duration

BLUE = 0x7AA2F7
TEAL = 0x73C6B6
PURPLE = 0xB4A1EB
AMBER = 0xD9B36C
RED = 0xDE8790


def clip(value: str, limit: int) -> str:
    value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value).strip()
    value = value.replace("@", "@\u200b")
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def prose(value: str, limit: int = 650) -> str:
    value = re.sub(r"(?m)^\s*#{1,6}\s+", "", value)
    value = value.replace("**", "").replace("`", "")
    return clip(value, limit)


plan_section = section


def bullets(values: list[str], limit: int = 500, count: int = 4) -> str:
    lines = ["• " + prose(re.sub(r"\s+", " ", value), 220) for value in values[:count]]
    if len(values) > count:
        lines.append(f"+ {len(values) - count} more in MarianaBot")
    return clip("\n".join(lines), limit)


def card(run: dict, title: str, description: str, fields: list[tuple], color=BLUE) -> dict:
    # Leave room for escaping Markdown in the SDK adapter (Discord allows 6,000 total).
    remaining = 2000
    formatted = []
    for entry in fields:
        name, value = entry[:2]
        if not value or remaining < 60:
            continue
        name = clip(name, 100)
        value = clip(value, min(650, remaining - len(name)))
        remaining -= len(name) + len(value)
        formatted.append(
            dict(name=name, value=value, inline=bool(entry[2]) if len(entry) > 2 else False)
        )
    footer = run["id"]
    return dict(
        title=clip(title, 180),
        description=prose(description, 240),
        color=color,
        fields=formatted,
        footer=dict(text=footer),
    )


def card_text(embed: dict) -> str:
    """Readable local preview and a text fallback for channels without Embed Links."""
    lines = [embed["title"], embed.get("description", "")]
    lines += [f"{field['name']}: {field['value']}" for field in embed.get("fields", [])]
    lines.append(embed.get("footer", {}).get("text", ""))
    return "\n\n".join(line for line in lines if line)


def token_text(row: dict) -> str:
    counts = [
        f"{row.get(kind + '_tokens', 0):,}"
        if row.get(kind + "_reports") or not row.get("calls")
        else "unknown"
        for kind in ("input", "output")
    ]
    return f"{counts[0]} in / {counts[1]} out" + (" · partial" if row.get("incomplete") else "")


def created_card(run: dict) -> dict:
    config = json.loads(run["config"])
    return card(
        run,
        "Research created",
        research_brief(run),
        [("Mode", config.get("research", {}).get("mode", "automatic"))],
    )


def question_cards(run: dict, payload: dict) -> list[dict]:
    rows = payload["questions"]
    result = []
    for offset in range(0, len(rows), 3):
        fields = [
            (
                row["question_id"],
                row["question"]
                + "\n"
                + "\n".join(f"{i}. {option}" for i, option in enumerate(row["options"], 1)),
            )
            for row in rows[offset : offset + 3]
        ]
        fields.append(
            (
                "Answer",
                "/mariana answer · select a question and answer choice, or type your answer.",
            )
        )
        result.append(
            card(
                run,
                f"Round {payload['number']} · Quick questions",
                research_brief(run),
                fields,
                BLUE,
            )
        )
    return result


def metrics(snapshot: dict) -> list[tuple]:
    timing = f"Elapsed {duration(snapshot.get('elapsed', 0))}"
    if snapshot.get("duration") is not None:
        timing = f"Round {duration(snapshot['duration'])} · " + timing
    usage = snapshot.get("round_usage", snapshot.get("usage", {}))
    tokens = "\n".join(
        f"{label}: {token_text(usage.get(provider, {}))}"
        for provider, label in (("openai", "ChatGPT"), ("anthropic", "Claude"))
    )
    return [
        ("Time", timing, True),
        ("Round tokens" if "round_usage" in snapshot else "Run tokens", tokens),
    ]


def next_step(review: dict) -> str:
    return sentence(review.get("next_step", "")) or bullets(review.get("online_checks", []), 320, 2)


def round_start_card(run: dict, payload: dict) -> dict:
    previous = payload.get("previous_review", {})
    focus = next_step(previous) or {
        "foundation": "Establish core facts and feasibility.",
        "validation": "Verify the remaining decision-changing uncertainties.",
        "decision": "Resolve contradictions and finish the recommendation.",
    }.get(payload.get("phase", "foundation"))
    fields = [("Focus", focus)]
    if previous:
        fields.append(
            (
                "Last review",
                f"{previous['score']}/100 · {previous['verdict'].replace('_', ' ')}",
                True,
            )
        )
    return card(
        run,
        f"Round {payload['number']}/{payload['max_rounds']} · Researching",
        research_brief(run),
        fields,
    )


def stage_card(run: dict, payload: dict) -> dict:
    stage, number = payload["stage"], payload.get("number", 0)
    update = payload.get("update", {})
    agents = payload.get("agents", 0)
    now = {
        "intake": "Preparing the objective and research questions.",
        "questions": "Preparing and checking this round's steering questions.",
        "synthesis": f"{agents} research proposals saved; RB is combining the findings.",
        "critique": f"{agents} independent JB reviewers are checking the evidence.",
        "verdict": f"{agents} critiques saved; JB is deciding what is resolved and what remains.",
    }.get(stage, "Research is progressing.")
    fields = [("Now", now)]
    plan = payload.get("plan", "")
    finding = sentence(update.get("summary", "")) or sentence(section(plan, "Round summary"))
    changes = bullets(update.get("changes", []), 360, 2) or sentence(
        section(plan, "Changes this round")
    )
    if finding:
        fields.append(("Finding", finding))
    if changes:
        fields.append(("Changed", changes))
    fields += metrics(payload)
    return card(
        run,
        f"Round {number} · {STAGES.get(stage, stage)}"
        if stage != "intake"
        else "Preparing research",
        research_brief(run),
        fields,
        PURPLE if stage in {"critique", "verdict"} else BLUE,
    )


def recap_card(run: dict, row: dict, snapshot: dict, previous: dict | None = None) -> dict:
    review = json.loads(row["review"]) if isinstance(row["review"], str) else row["review"]
    finding = sentence(review.get("summary", "")) or sentence(section(row["plan"], "Round summary"))
    if not finding:
        finding = sentence(
            next(
                iter(review.get("strengths", [])),
                "Review completed; see the remaining issues below.",
            )
        )
    changes = review.get("changes", [])
    if not changes and section(row["plan"], "Changes this round"):
        changes = [section(row["plan"], "Changes this round")]
    if not changes and previous:
        before, after = previous.get("blocking_issues", []), review.get("blocking_issues", [])
        changes = ["No longer listed: " + item for item in before if item not in after][:1]
        changes += ["Now flagged: " + item for item in after if item not in before][:1]
        changes += [
            item
            for item in review.get("strengths", [])
            if item not in previous.get("strengths", [])
        ][:1]
    score = f"{review.get('score', '?')}/100 · {review.get('verdict', 'unknown').replace('_', ' ')}"
    if previous:
        score += f" ({review['score'] - previous['score']:+d})"
    fields = [
        ("Finding", finding),
        ("Changed", bullets(changes, 400, 3)),
        ("Open", bullets(review.get("blocking_issues", []), 320, 2)),
        ("Unverified", bullets(review.get("limitations", []), 260, 1)),
        ("Next", next_step(review)),
        *metrics(snapshot),
    ]
    return card(
        run,
        f"Round {row['number']} complete · {score}",
        research_brief(run),
        fields,
        TEAL if review.get("verdict") == "approve" else AMBER,
    )


def message_card(run: dict, row: dict) -> dict:
    if row.get("message_key") == "intake":
        return card(
            run, "Research brief ready", research_brief(run | {"brief": row["text"]}), [], TEAL
        )
    return card(
        run,
        "MB · " + row["title"],
        research_brief(run),
        [("Response", prose(row["text"], 650))],
        TEAL,
    )


def state_card(run: dict, snapshot: dict, review: dict | None = None) -> dict:
    progress = snapshot.get(
        "progress",
        {"number": run.get("round", 0), "stage": "saved", "completed": run.get("round", 0)},
    )
    reason = run.get("reason", "") or "Research is active."
    if reason == "Codex account/rateLimits/read failed; check login, network and CLI version":
        reason = "Codex's usage check failed before the next model request. This version did not save the server's error detail."
    fields = [("What happened", reason)]
    if run["status"] == "awaiting_answers":
        fields += [
            (
                "Next",
                "Answer the round questions in MarianaBot or with /mariana answer. The final answer continues the round.",
            )
        ]
    elif run["status"] == "paused":
        fields += [
            ("Stopped at", f"{STAGES[progress['stage']]} · {progress['completed']} rounds saved"),
            ("Continue", "/mariana resume"),
        ]
    elif review:
        fields.append(
            (
                "Conclusion",
                sentence(review.get("summary", ""))
                or sentence(next(iter(review.get("strengths", [])), "")),
            )
        )
    fields += metrics(snapshot)
    return card(
        run,
        f"Research {run['status'].replace('_', ' ')} · Round {progress['number']}",
        research_brief(run),
        fields,
        TEAL if run["status"] == "complete" else AMBER,
    )


def wait_card(run: dict, payload: dict) -> dict:
    resumed = payload.get("resumed", False)
    return card(
        run,
        "Research continuing" if resumed else "Research waiting",
        research_brief(run),
        [("Reason", payload["reason"]), ("Wait", duration(payload["seconds"]))]
        if not resumed
        else [],
        BLUE if resumed else AMBER,
    )
