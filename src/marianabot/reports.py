import json
import os
import re
from pathlib import Path

from marianabot.store import Store


def atomic_text(path: Path, content: str):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def export_run(store: Store, run_id: str, target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    run = store.run(run_id)
    rounds = store.rounds(run_id)
    commands = store.commands(run_id)
    calls = store.calls(run_id)
    mode = "OFFLINE DEMO — illustrative fixtures only" if run["demo"] else "Subscription research"
    report = [
        f"# MarianaBot · {run_id}",
        mode,
        f"Status: {run['status']} — {run['reason']}",
        "## Original problem",
        run["problem"],
        "## Current research brief",
        run["brief"],
    ]
    if rounds:
        last = rounds[-1]
        report += [
            f"## Latest plan · round {last['number']}",
            last["plan"],
            "## Latest critical review",
            f"Reviewer score: {last['review']['score']}/100; verdict: {last['review']['verdict']}.",
        ]
        for field in ("strengths", "blocking_issues", "human_tests", "dissent"):
            report += [
                f"### {field.replace('_', ' ').title()}",
                "\n".join("- " + item for item in last["review"][field]) or "None reported.",
            ]
        report += [
            "### Next research prompt",
            last["review"]["next_prompt"],
            "## Iteration history",
            "| Round | Revision | Score | Verdict |\n|---|---|---|---|",
        ]
        report += [
            f"| {r['number']} | {r['revision']} | {r['review']['score']} | {r['review']['verdict']} |"
            for r in rounds
        ]
    if commands:
        report.append("## Owner conversation")
        for command in commands:
            report += [
                f"### {command['kind']} #{command['id']}",
                command["text"],
                command["answer"] or "Pending MB response.",
            ]
    report += [
        "## Interpretation",
        "Model agreement and reviewer scores do not establish market demand or factual accuracy. "
        "Use the listed experiments and primary sources to test the plan.",
    ]
    atomic_text(target / "report.md", "\n\n".join(report) + "\n")
    atomic_text(
        target / "history.json",
        json.dumps(
            {
                "run": run,
                "rounds": rounds,
                "commands": commands,
                "calls": calls,
                "reported_usage": store.usage_totals(run_id),
                "conversation": store.messages(run_id),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    atomic_text(
        target / "conversation.md",
        "\n\n".join(f"## {m['role']} · {m['title']}\n\n{m['text']}" for m in store.messages(run_id))
        + "\n",
    )
    urls = {}
    for call in calls:
        if call["result"]:
            for url in re.findall(r'https?://[^\s<>"\)]+', call["result"].get("text", "")):
                urls.setdefault(url.rstrip(".,;]"), set()).add(call["brain"])
    evidence = [
        "# Citation index",
        "These are URLs cited by models, not independently verified sources.",
        "| URL | Cited by |\n|---|---|",
    ]
    evidence += [
        f"| {url.replace('|', '%7C')} | {', '.join(sorted(brains))} |"
        for url, brains in sorted(urls.items())
    ]
    atomic_text(target / "citations.md", "\n\n".join(evidence) + "\n")
    return target / "report.md"
