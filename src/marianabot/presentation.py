"""Compact display text from saved briefs, progress and review metadata."""

import json
import re


def sentence(value: str, limit=220) -> str:
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"[*`#]", "", value)
    value = re.sub(r"\s+", " ", value).strip(" -:•")
    value = re.split(r"(?<=[.!?])\s+(?=[A-Z])", value, maxsplit=1)[0]
    if len(value) > limit:
        value = value[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return value


def research_brief(run: dict) -> str:
    contract = json.loads(run.get("contract") or "{}")
    if contract.get("objective"):
        return sentence(contract["objective"])
    brief = run.get("brief", "")
    for label in ("Research question", "Owner objective", "Objective", "Decision"):
        match = re.search(rf"(?im)^\s*(?:\*\*|#+\s*)?{label}\s*[:：]?\*?\*?\s*([^\n]+)", brief)
        if match and (text := sentence(match[1])):
            return text
    problem = run.get("problem", "").strip()
    if (
        problem
        and len(problem) < 500
        and not re.search(
            r"(?i)system role|directive|master.*prompt|you are|={3,}|\[system", problem
        )
    ):
        return sentence(problem)
    return "Preparing a concise research brief."


def research_title(run: dict) -> str:
    contract = json.loads(run.get("contract") or "{}")
    if contract.get("title"):
        return contract["title"][:60]
    text = re.sub(
        r"(?i)^(?:assess|evaluate|research|investigate)\s+(?:whether\s+)?(?:the\s+)?(?:supplied\s+)?(?:concept\s+for\s+)?",
        "",
        research_brief(run),
    )
    return " ".join(text.split()[:6]).strip(".,;:")


def section(plan: str, heading: str) -> str:
    pattern = rf"(?im)^\s*(?:#{{1,6}}\s+|\*\*){re.escape(heading)}\s*(?:\*\*)?\s*:?\s*\n(.*?)(?=^\s*(?:#{{1,6}}\s|\*\*[A-Z])|\Z)"
    match = re.search(pattern, plan, re.S)
    return match[1].strip() if match else ""


def update_metadata(text: str) -> tuple[str, dict]:
    match = re.search(r"<research_update>\s*(.*?)\s*</research_update>", text, re.S)
    if not match:
        return text, {}
    try:
        data = json.loads(match[1])
        if not isinstance(data, dict):
            return text, {}
        cleaned = {
            key: sentence(data[key])
            for key in ("summary", "next_step")
            if isinstance(data.get(key), str)
        }
        cleaned["changes"] = (
            [sentence(item) for item in data.get("changes", [])[:3] if isinstance(item, str)]
            if isinstance(data.get("changes"), list)
            else []
        )
    except ValueError:
        return text, {}
    memo = (text[: match.start()] + text[match.end() :]).strip()
    return (memo, cleaned) if memo else (text, {})


STAGES = {
    "questions": "Round questions",
    "intake": "Preparing brief",
    "research": "Researching",
    "synthesis": "Combining findings",
    "critique": "Reviewing evidence",
    "verdict": "Review decision",
    "memory": "Preparing context",
    "account": "Reconnecting Codex",
    "saved": "Round saved",
}
