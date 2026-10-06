"""Render synthetic notification cards locally; no research, credentials or network."""

import argparse
import html
import json
import textwrap
from pathlib import Path

from marianabot.config import Config
from marianabot.discord_messages import recap_card, round_start_card, stage_card


def fixtures() -> list[dict]:
    config = Config()
    config.rb.model, config.rb.effort = "gpt-6.1-sol", "xhigh"
    config.jb.effort = "high"
    run = dict(
        id="preview-only",
        problem="Evaluate a paid pilot for a local repair scheduling service.",
        config=config.model_dump_json(),
        demo=True,
    )
    plan = """## Round summary
Compared small repair shops with mobile technicians. The current proposal prioritizes shops with repeat bookings and a measurable scheduling problem.
## Changes this round
Reduced the pilot to five shops. Added a two-week trial, a fixed spending ceiling and a clear cancellation threshold.
## Next direction
Check whether buyers will pay before expanding the pilot.
## Full plan
This is synthetic preview material, not actual research.
"""
    review = dict(
        score=78,
        verdict="revise",
        next_prompt="Compare current scheduling costs with the proposed price, then define paid pilot acceptance criteria.",
        blocking_issues=[
            "Willingness to pay remains untested",
            "Acquisition costs lack a reliable baseline",
        ],
        strengths=["Pilot scope and spending now have explicit limits"],
        human_tests=["Ask three shop owners for paid pilot commitments"],
        dissent=["Booking volume may be too low to justify a separate tool"],
    )
    roles = [
        "Market researcher: demand and competition",
        "Business economist: pricing and unit economics",
        "Operator: pilot milestones and dependencies",
    ]
    return [
        round_start_card(
            run,
            dict(
                number=2,
                max_rounds=24,
                focus=review["next_prompt"],
                roles=roles,
                previous_review=review | dict(score=71),
            ),
        ),
        stage_card(
            run,
            dict(stage="synthesis", number=2, agents=3, focus=review["next_prompt"], elapsed=120),
        ),
        stage_card(
            run,
            dict(
                stage="critique",
                number=2,
                agents=3,
                plan=plan,
                elapsed=240,
                roles=[
                    "Skeptical investor: economics and downside",
                    "Hostile customer: switching costs and alternatives",
                    "Failure investigator: bottlenecks and unknowns",
                ],
            ),
        ),
        stage_card(
            run,
            dict(
                stage="verdict",
                number=2,
                agents=3,
                elapsed=300,
                critiques=[
                    "Demand evidence remains weak; a paid commitment is the next useful test.",
                    "The trial needs a spending ceiling and explicit cancellation criteria.",
                    "Low booking volume could make manual scheduling the cheaper option.",
                ],
            ),
        ),
        recap_card(
            run,
            dict(number=2, plan=plan, review=review),
            dict(elapsed=360, duration=180, usage={}, round_usage={}),
            review | dict(score=71),
        ),
    ]


def html_card(embed: dict) -> str:
    escape = html.escape
    fields = "".join(
        f'<div class="field {"inline" if field["inline"] else "wide"}"><b>{escape(field["name"])}</b><p>{escape(field["value"])}</p></div>'
        for field in embed["fields"]
    )
    return f'<article style="border-color:#{embed["color"]:06x}"><h2>{escape(embed["title"])}</h2><p>{escape(embed["description"])}</p><div class="fields">{fields}</div><footer>{escape(embed["footer"]["text"])}</footer></article>'


def svg_card(embed: dict) -> str:
    """One representative card for a portable visual review."""
    elements, y = [], 58

    def text(value: str, *, bold=False, muted=False, width=76):
        nonlocal y
        for paragraph in value.splitlines():
            for line in textwrap.wrap(paragraph, width) or [""]:
                elements.append(
                    f'<text x="38" y="{y}" font-size="{14 if bold else 13}" font-weight="{600 if bold else 400}" fill="{"#aab0bb" if muted else "#e1e3e8"}">{html.escape(line)}</text>'
                )
                y += 20

    text(embed["title"], bold=True)
    y += 6
    text(embed["description"])
    for field in embed["fields"]:
        y += 14
        text(field["name"], bold=True)
        text(field["value"])
    y += 18
    text(embed["footer"]["text"], muted=True)
    height = y + 22
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="680" height="{height}" font-family="Segoe UI, sans-serif"><rect width="680" height="{height}" fill="#1e1f22"/><rect x="20" y="20" width="640" height="{height - 40}" rx="5" fill="#2b2d31"/><rect x="20" y="20" width="4" height="{height - 40}" rx="2" fill="#{embed["color"]:06x}"/>{"".join(elements)}</svg>'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path(".mariana/visual-review/discord-updates.html")
    )
    path = parser.parse_args().output
    cards = fixtures()
    stylesheet = """
body{margin:0;background:#1e1f22;color:#e1e3e8;font:15px/1.5 'Segoe UI',sans-serif}
main{max-width:660px;margin:40px auto;padding:0 20px}h1{font-size:23px;margin-bottom:6px}
.note{color:#aab0bb;margin-bottom:26px}article{background:#2b2d31;border-left:4px solid;border-radius:4px;padding:18px 20px;margin:22px 0}
h2{font-size:17px;margin:0 0 8px}p{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}
.fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-top:18px}
.wide{grid-column:1/-1}.field b{display:block;font-size:14px;margin-bottom:3px}
footer{font-size:11px;color:#aab0bb;margin-top:20px}@media(max-width:520px){.fields{grid-template-columns:1fr}.wide{grid-column:auto}}
"""
    document = (
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MarianaBot Discord preview</title><style>'
        + stylesheet
        + '</style><main><h1>MarianaBot · Discord updates</h1><p class="note">Synthetic layout preview. No research or model calls were made.</p>'
        + "".join(html_card(embed) for embed in cards)
        + "</main></html>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    path.with_suffix(".json").write_text(
        json.dumps(cards, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    path.with_suffix(".svg").write_text(svg_card(cards[-1]), encoding="utf-8")
    print(path.resolve())


if __name__ == "__main__":
    main()
