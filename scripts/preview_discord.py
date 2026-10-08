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
        id="MB-7K3M-9Q2R-5V8N",
        problem="Assess demand and competition for a repair scheduling service with online evidence.",
        config=config.model_dump_json(),
        demo=True,
    )
    plan = """## Round summary
Compared small repair shops with mobile technicians. The current proposal prioritizes shops with repeat bookings and a measurable scheduling problem.
## Changes this round
Narrowed the comparison to tools aimed at small shops. Added published price ranges and labeled unsupported demand claims.
## Next direction
Compare documented prices and public buyer reviews.
## Full plan
This is synthetic preview material, not actual research.
"""
    review = dict(
        score=78,
        verdict="revise",
        next_prompt="Compare public scheduling prices and buyer reviews; disclose what they cannot prove.",
        blocking_issues=[
            "Willingness to pay remains untested",
            "Acquisition costs lack a reliable baseline",
        ],
        strengths=["The scope stays within the original question and budget"],
        human_tests=[],
        online_checks=["Verify current competitor prices and review patterns"],
        limitations=["Public interest does not prove willingness to pay"],
        dissent=["Booking volume may be too low to justify a separate tool"],
    )
    roles = [
        "Market researcher: demand and competition",
        "Business economist: pricing and unit economics",
        "Operator: basic feasibility and dependencies",
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
                    "Public reviews show interest but cannot establish willingness to pay.",
                    "Current prices need verification against published listings.",
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

    def text(value: str, *, bold=False, muted=False, width=76, x=38):
        nonlocal y
        for paragraph in value.splitlines():
            for line in textwrap.wrap(paragraph, width) or [""]:
                elements.append(
                    f'<text x="{x}" y="{y}" font-size="{14 if bold else 13}" font-weight="{600 if bold else 400}" fill="{"#aab0bb" if muted else "#e1e3e8"}">{html.escape(line)}</text>'
                )
                y += 20

    text(embed["title"], bold=True)
    y += 6
    text(embed["description"])
    fields = iter(embed["fields"])
    pending = next(fields, None)
    while pending:
        y += 14
        if not pending["inline"]:
            text(pending["name"], bold=True)
            text(pending["value"])
            pending = next(fields, None)
            continue
        row = []
        while pending and pending["inline"] and len(row) < 3:
            row.append(pending)
            pending = next(fields, None)
        top, bottom = y, y
        for column, field in enumerate(row):
            y = top
            text(field["name"], bold=True, width=26, x=38 + column * 202)
            text(field["value"], width=26, x=38 + column * 202)
            bottom = max(bottom, y)
        y = bottom
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
    svg = svg_card(cards[-1])
    path.with_suffix(".svg").write_text(svg, encoding="utf-8")
    try:
        import resvg_py
    except ImportError:
        print("Install resvg-py to also render the recap as a PNG.")
    else:
        path.with_suffix(".png").write_bytes(resvg_py.svg_to_bytes(svg_string=svg))
    print(path.resolve())


if __name__ == "__main__":
    main()
