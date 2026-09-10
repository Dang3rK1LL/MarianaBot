from datetime import UTC, datetime

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from marianabot.store import Store


def dashboard(store: Store, run_id: str):
    run = store.run(run_id)
    calls = store.calls(run_id)
    rounds = store.rounds(run_id)
    mode = "OFFLINE DEMO · no model calls" if run["demo"] else "SUBSCRIPTION MODE"
    title = Text("M A R I A N A B O T", style="bold bright_cyan")
    title.append(f"\n{mode}    {run_id}", style="dim")
    summary = Text(
        f"{run['status'].upper()}  ·  {run['round']} rounds saved  ·  brief revision {run['revision']}"
    )
    if run["reason"]:
        summary.append("\n" + run["reason"], style="yellow")

    brains = Table(box=box.SIMPLE, expand=True)
    brains.add_column("Brain", style="bold")
    brains.add_column("Court")
    brains.add_column("Saved", justify="right")
    brains.add_column("Current work", overflow="fold")
    for name, role, color in (
        ("MB", "Coordinator", "bright_magenta"),
        ("RB", "Research", "bright_cyan"),
        ("JB", "Judgment", "bright_yellow"),
    ):
        own = [c for c in calls if c["brain"] == name]
        active = [c["task"] for c in own if c["state"] == "running"]
        brains.add_row(
            Text(name, style=color),
            role,
            str(sum(c["state"] == "done" for c in own)),
            "\n".join(active) if active else "Waiting",
        )

    quotas = Table(box=box.SIMPLE, expand=True)
    quotas.add_column("Shared usage")
    quotas.add_column("Last reported")
    quotas.add_column("Reset / next attempt (UTC)")
    for provider, name in (("openai", "MB + RB · ChatGPT"), ("anthropic", "JB · Claude")):
        info = store.get_limits(provider) if not run["demo"] else {}
        windows = info.get("windows", [])
        if not windows:
            quotas.add_row(name, "Not reported" if not run["demo"] else "Demo", "—")
        for window in windows:
            percent = window.get("percent")
            reset = window.get("reset")
            stamp = (
                datetime.fromtimestamp(reset, UTC).strftime("%b %d %H:%M") if reset else "Unknown"
            )
            quotas.add_row(
                name,
                f"{percent:.0f}% · {window['name']}" if percent is not None else "Unknown",
                stamp,
            )
        if info.get("until", 0) > datetime.now(UTC).timestamp():
            quotas.add_row(
                "",
                "Cooling down",
                datetime.fromtimestamp(info["until"], UTC).strftime("%b %d %H:%M"),
            )

    history = Text()
    if rounds:
        history.append("Review scores  ", style="dim")
        history.append(
            "  →  ".join(str(r["review"]["score"]) for r in rounds[-10:]), style="bold cyan"
        )
        history.append(
            "\nScores are model judgments, not validated business outcomes.", style="dim"
        )
    timeline = Text()
    for event in reversed(store.events(run_id, 8)):
        stamp = datetime.fromtimestamp(event["created"], UTC).strftime("%H:%M:%S")
        timeline.append(f"{stamp}  ", style="dim")
        timeline.append(event["text"] + "\n")
    return Group(
        Panel(title, border_style="bright_cyan"),
        summary,
        brains,
        quotas,
        history,
        Panel(timeline, title="Dive log", border_style="blue"),
        Text(
            "Another terminal: mariana ask / steer / pause / stop     Ctrl+C: pause safely",
            style="dim",
        ),
    )
