"""A fixed, quiet usage strip; no fabricated balances or token estimates."""

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from marianabot.limits import finite

TEXT = "#d9e0e4"
MUTED = "#a6afb8"
ATTENTION = "#dfba79"


def duration(seconds: float) -> str:
    seconds = max(0, seconds)
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h {int(seconds % 3600 / 60):02}m"
    return f"{int(seconds / 86400)}d {int(seconds % 86400 / 3600)}h"


def age(observed, now: float) -> str:
    return duration(now - finite(observed)) + " ago" if observed else "not yet"


def window_label(window: dict) -> str:
    minutes = finite(window.get("duration_minutes"))
    if minutes:
        if minutes % 1440 == 0:
            return f"{int(minutes / 1440)}d"
        return f"{minutes / 60:g}h" if minutes >= 60 else f"{minutes:g}m"
    name = str(window.get("name", "window"))
    return {
        "five_hour": "5h",
        "seven_day": "7d",
        "seven_day_opus": "Opus 7d",
        "seven_day_sonnet": "Sonnet 7d",
    }.get(name, name.removeprefix("codex ").replace("_", " ")[:18])


def quota_line(data: dict, *, demo=False, now=None) -> Text:
    now = time.time() if now is None else now
    if demo:
        return Text("  Offline demo · no subscription usage", style=MUTED)
    windows = data.get("windows", [])
    if not windows:
        line = Text("  Allowance not reported", style=MUTED)
        wait = finite(data.get("until")) - now
        if wait > 0:
            line.append(f" · waiting {duration(wait)}", style=ATTENTION)
        return line
    # Show the highest-usage windows first, so a blocking weekly limit stays visible.
    windows = sorted(windows, key=lambda w: finite(w.get("percent"), -1), reverse=True)
    line = Text("  ", style=MUTED)
    for index, window in enumerate(windows[:2]):
        if index:
            line.append(" · ")
        percent = window.get("percent")
        reset = finite(window.get("reset"))
        stale = reset and reset <= now
        label = window_label(window)
        used = (
            f"{finite(percent):.0f}%"
            if percent is not None
            else str(window.get("status", "unknown"))
        )
        style = ATTENTION if stale or finite(percent) >= 95 else MUTED
        line.append(f"{label} {used}", style=style)
    if len(windows) > 2:
        line.append(f" +{len(windows) - 2}")
    wait = finite(data.get("until")) - now
    if wait > 0:
        line.append(f" · waiting {duration(wait)}", style=ATTENTION)
    else:
        resets = [finite(w.get("reset")) for w in windows if finite(w.get("reset"))]
        if resets:
            reset = min(resets)
            line.append(
                " · refresh due" if reset <= now else f" · next reset {duration(reset - now)}",
                style=ATTENTION if reset <= now else MUTED,
            )
    observed = min(
        (finite(w.get("observed"), finite(data.get("observed"))) for w in windows), default=0
    )
    line.append(f" · seen {age(observed, now)}")
    return line


class UsageStrip(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("Usage", id="usage-scope", markup=False)
        yield Static("", id="usage-openai", markup=False)
        yield Static("", id="limit-openai", markup=False)
        yield Static("", id="usage-anthropic", markup=False)
        yield Static("", id="limit-anthropic", markup=False)

    def update_usage(
        self, totals: dict, limits: dict, *, scope: str, demo: bool, working: bool, now=None
    ):
        now = time.time() if now is None else now
        wide = self.size.width >= 100
        self.query_one("#usage-scope", Static).update(
            scope + " · reported tokens, incl. cache · allowance used"
        )
        for provider, label in (("openai", "ChatGPT MB+RB"), ("anthropic", "Claude  JB")):
            row = totals.get(provider, {})
            calls = row.get("calls", 0)
            count = row.get("active", 0) if working else 0
            incoming = (
                f"{row.get('input_tokens', 0):,}" if row.get("input_reports") or not calls else "—"
            )
            outgoing = (
                f"{row.get('output_tokens', 0):,}"
                if row.get("output_reports") or not calls
                else "—"
            )
            line = Text(f"{label:<14}", style=TEXT)
            line.append(f" {incoming} in  /  {outgoing} out", style=TEXT)
            if count:
                state = f"{count} active"
                if row.get("incomplete"):
                    state += " · partial"
            elif row.get("incomplete"):
                state = "partial totals"
            else:
                state = "idle" if working else "saved" if calls else "idle"
            line.append(f"  ·  {state}", style=MUTED)
            if wide and row.get("reported_at"):
                line.append(f" · {age(row['reported_at'], now)}", style=MUTED)
            widget = self.query_one(f"#usage-{provider}", Static)
            widget.update(line)
            widget.tooltip = (
                f"Reported input: {incoming}; output: {outgoing}. Input includes cache.\n"
                f"Cache read: {row.get('cache_read_tokens', 0):,}; cache write: {row.get('cache_write_tokens', 0):,}.\n"
                f"{row.get('reported_calls', 0)} of {calls} calls have usage reports. Last report: {age(row.get('reported_at'), now)}.\n"
                "Counts belong to this MarianaBot run, not other apps using your subscriptions."
            )
            quota = self.query_one(f"#limit-{provider}", Static)
            quota.update(quota_line(limits.get(provider, {}), demo=demo, now=now))
            quota.tooltip = "Allowance percentages are used, not remaining.\n" + "\n".join(
                quota_line({**limits.get(provider, {}), "windows": [window]}, now=now).plain.strip()
                for window in limits.get(provider, {}).get("windows", [])
            )
