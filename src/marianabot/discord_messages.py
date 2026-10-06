"""Research notification cards, built from saved output without additional inference."""

import json
import re

from marianabot.config import Config
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


def plan_section(plan: str, heading: str) -> str:
    match = re.search(rf"(?im)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s|\Z)", plan, re.S)
    return match[1].strip() if match else ""


def bullets(values: list[str], limit: int = 500, count: int = 4) -> str:
    lines = ["• " + prose(re.sub(r"\s+", " ", value), 220) for value in values[:count]]
    if len(values) > count:
        lines.append(f"+ {len(values) - count} more in MarianaBot")
    return clip("\n".join(lines), limit)


def card(run: dict, title: str, description: str, fields: list[tuple], color=BLUE) -> dict:
    # Leave room for escaping Markdown in the SDK adapter (Discord allows 6,000 total).
    remaining = 4200
    formatted = []
    for entry in [("Research ID", run["id"], True), *fields]:
        name, value = entry[:2]
        if not value or remaining < 60:
            continue
        name = clip(name, 100)
        value = clip(value, min(900, remaining - len(name)))
        remaining -= len(name) + len(value)
        formatted.append(
            dict(name=name, value=value, inline=bool(entry[2]) if len(entry) > 2 else False)
        )
    footer = f"Research {run['id']} · Full detail in MarianaBot"
    if run["demo"]:
        footer = "Offline demo · fixture data; no model usage · " + footer
    return dict(
        title=clip(title, 180),
        description=prose(description, 650),
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
    config = Config.model_validate_json(run["config"])
    return card(
        run,
        "Research created",
        run["problem"],
        [
            (
                "Models",
                f"MB/RB: {config.rb.model} · {config.rb.effort}\nJB: {config.jb.model} · {config.jb.effort}",
            ),
            (
                "Updates",
                "Follow the brief, independent research, reviews and round recaps in this channel.",
            ),
        ],
    )


def metrics(snapshot: dict) -> list[tuple]:
    timing = f"Elapsed: {duration(snapshot.get('elapsed', 0))}"
    if snapshot.get("duration") is not None:
        timing = f"Round: {duration(snapshot['duration'])}\n" + timing
    fields = [("Time", timing + "\nIncludes pauses and waits", True)]
    for provider, label in (("openai", "ChatGPT"), ("anthropic", "Claude")):
        row = snapshot.get("usage", {}).get(provider, {})
        text = "Run tokens · " + label + ": " + token_text(row)
        if "round_usage" in snapshot:
            text = (
                "Round team: " + token_text(snapshot["round_usage"].get(provider, {})) + "\n" + text
            )
        if row.get("cache_read_tokens") or row.get("cache_write_tokens"):
            text += f"\nRun cache: {row.get('cache_read_tokens', 0):,} read / {row.get('cache_write_tokens', 0):,} written"
        fields.append((label + " tokens", text, True))
    return fields


def round_start_card(run: dict, payload: dict) -> dict:
    config = Config.model_validate_json(run["config"])
    fields = [
        ("Research focus", prose(payload.get("focus", run["problem"]), 650)),
        (
            "Research team",
            bullets([f"{i + 1}. {role}" for i, role in enumerate(payload.get("roles", []))], 700),
        ),
        (
            "Process",
            f"{config.rb.agents} independent proposals → research chair → {config.jb.agents} independent critiques → review chair",
        ),
        (
            "Models",
            f"MB/RB: {config.rb.model} · {config.rb.effort}\nJB: {config.jb.model} · {config.jb.effort}",
        ),
        (
            "Retrieval",
            "Web search enabled for research and critique calls"
            if config.research.web_search
            else "Web search disabled; using supplied evidence",
        ),
    ]
    previous = payload.get("previous_review", {})
    if previous:
        fields.insert(
            1,
            (
                "Previous review",
                f"{previous['score']}/100 · {previous['verdict'].replace('_', ' ')}",
            ),
        )
        fields.insert(2, ("Issues to address", bullets(previous.get("blocking_issues", []))))
    return card(
        run, f"Round {payload['number']}/{payload['max_rounds']} started", run["problem"], fields
    )


def stage_card(run: dict, payload: dict) -> dict:
    stage, number = payload["stage"], payload.get("number", 0)
    fields = []
    if stage == "intake":
        title = "Preparing the research brief"
        description = (
            "MB is defining the objective, constraints, working assumptions and evidence needed."
        )
        fields = [("Research question", prose(run["problem"], 900))]
        color = BLUE
    elif stage == "synthesis":
        title = f"Round {number} · Plan synthesis"
        description = f"{payload['agents']} independent research proposals are saved. The research chair is comparing their recommendations and assembling the plan."
        fields = [
            ("Current focus", prose(payload.get("focus", run["problem"]))),
            (
                "What happens next",
                "Select supported findings, preserve disagreements, and explain changes to the previous plan.",
            ),
        ]
        color = BLUE
    elif stage == "critique":
        title = f"Round {number} · Independent critique"
        description = f"The research plan is ready. {payload['agents']} independent judges are checking the evidence, economics and execution risks."
        plan = payload.get("plan", "")
        fields = [
            (
                "Findings so far" if plan_section(plan, "Round summary") else "Plan excerpt",
                prose(plan_section(plan, "Round summary") or plan),
            ),
            (
                "Changes proposed",
                prose(
                    plan_section(plan, "Changes this round")
                    or "No separate change summary recorded.",
                    550,
                ),
            ),
            (
                "Review team",
                bullets(
                    [f"{i + 1}. {role}" for i, role in enumerate(payload.get("roles", []))], 700
                ),
            ),
        ]
        color = PURPLE
    else:
        title = f"Round {number} · Review decision"
        description = f"{payload['agents']} independent critiques are saved. The review chair is weighing objections and deciding whether the plan needs revision or human evidence."
        fields = [
            (f"Critique {i + 1} · excerpt", prose(value, 350))
            for i, value in enumerate(payload.get("critiques", [])[:3])
        ]
        color = PURPLE
    fields += metrics(payload)
    return card(run, title, description, fields, color)


def recap_card(run: dict, row: dict, snapshot: dict, previous: dict | None = None) -> dict:
    review = json.loads(row["review"]) if isinstance(row["review"], str) else row["review"]
    plan = row["plan"]
    summary = plan_section(plan, "Round summary")
    score = f"Review {review.get('score', '?')}/100 · {review.get('verdict', 'unknown').replace('_', ' ')}"
    if previous:
        delta = review["score"] - previous["score"]
        score += f" · {delta:+d} since previous round"
    fields = [
        ("Recap" if summary else "Plan excerpt", prose(summary or plan, 700)),
        (
            "Changed",
            prose(
                plan_section(plan, "Changes this round") or "No separate change summary recorded.",
                650,
            ),
        ),
        ("Next", prose(review.get("next_prompt", "Not recorded."), 600)),
        (
            "Open issues",
            bullets(review.get("blocking_issues", []), 550) or "No blocking issues reported.",
        ),
        ("Strengths", bullets(review.get("strengths", []), 350, 2)),
        ("Needs your input", bullets(review.get("human_tests", []), 450, 3)),
        ("Disagreements retained", bullets(review.get("dissent", []), 350, 2)),
    ]
    fields += metrics(snapshot)
    color = {"approve": TEAL, "revise": AMBER, "needs_human": RED}.get(review.get("verdict"), BLUE)
    return card(run, f"Round {row['number']} complete", score, fields, color)


def message_card(run: dict, row: dict) -> dict:
    brief = row.get("message_key") == "intake"
    return card(
        run,
        "Research brief ready" if brief else "MB · " + row["title"],
        "Objective, constraints and working assumptions for the research team."
        if brief
        else "Master brain reply",
        [("Brief" if brief else "Response", prose(row["text"], 1800))],
        TEAL,
    )


def state_card(run: dict, snapshot: dict, review: dict | None = None) -> dict:
    fields = [("Reason", prose(run["reason"] or "Research worker is active.", 650))]
    if review:
        fields += [
            ("Next direction", prose(review.get("next_prompt", ""), 500)),
            ("Needs your input", bullets(review.get("human_tests", []), 450)),
        ]
    fields += metrics(snapshot)
    return card(
        run,
        f"Research {run['status']} · round {run['round']}",
        run["problem"],
        fields,
        TEAL if run["status"] == "complete" else AMBER,
    )


def wait_card(run: dict, payload: dict) -> dict:
    resumed = payload.get("resumed", False)
    return card(
        run,
        "Research continuing" if resumed else "Research waiting",
        "The wait has finished; queued work can continue."
        if resumed
        else "Research is waiting before the next model request.",
        [
            ("Reason", prose(payload["reason"], 650)),
            ("Wait", duration(payload["seconds"])),
            (
                "Next",
                "Continue from the saved checkpoint."
                if resumed
                else "Continue automatically when the wait ends. Completed work is saved.",
            ),
        ],
        BLUE if resumed else AMBER,
    )
