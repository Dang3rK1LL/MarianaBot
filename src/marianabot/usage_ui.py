"""A fixed, quiet usage strip; no fabricated balances or token estimates."""

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Static

from marianabot.config import Config
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


def quota_line(data: dict, *, now=None) -> Text:
    now = time.time() if now is None else now
    windows = data.get("windows", [])
    if not windows:
        line = Text("  Allowance not reported", style=MUTED)
        wait = finite(data.get("until")) - now
        if wait > 0:
            line.append(f" · waiting {duration(wait)}", style=ATTENTION)
        return line
    # Keep the busiest windows visible, with the 5-hour window displayed first.
    windows = sorted(windows, key=lambda w: finite(w.get("percent"), -1), reverse=True)
    displayed = sorted(windows[:2], key=lambda w: window_label(w) != "5h")
    line = Text("  ", style=MUTED)
    for index, window in enumerate(displayed):
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


class UsageStrip(VerticalScroll):
    def compose(self) -> ComposeResult:
        with Horizontal(id="usage-heading"):
            yield Static("Usage", id="usage-scope", markup=False)
            yield Button("Refresh", id="refresh-usage")
        yield Static("", id="research-overview", markup=False)
        yield Static("", id="usage-models", markup=False)
        yield Static("", id="usage-openai", markup=False)
        yield Static("", id="limit-openai", markup=False)
        yield Static("", id="usage-anthropic", markup=False)
        yield Static("", id="limit-anthropic", markup=False)

    def update_usage(
        self,
        totals: dict,
        limits: dict,
        *,
        scope: str,
        config: Config | None,
        working: bool,
        now=None,
        refresh: dict | None = None,
        selections: dict | None = None,
        run: dict | None = None,
        memory: dict | None = None,
    ):
        now = time.time() if now is None else now
        wide = self.app.size.width >= 120
        self.query_one("#usage-scope", Static).update(
            "Usage · background" if scope.startswith("Working run") else "Usage"
        )
        self.query_one("#usage-scope", Static).tooltip = (
            scope + "; tokens for this research, account allowance shown as used."
        )
        overview = self.query_one("#research-overview", Static)
        overview.update(f"Elapsed {duration(now - run['created'])}" if run else "")
        overview.tooltip = "Elapsed time includes pauses and waits."
        models = Text("Models", style=MUTED)
        if config:
            for label, brain in (("MB", config.mb), ("RB", config.rb), ("JB", config.jb)):
                selected = (selections or {}).get(label)
                model = selected["model"] if selected else brain.model
                effort = (selected.get("effort") or "unknown") if selected else brain.effort
                models.append(f"\n{label:<3}{model} / {effort}", style=TEXT)
        widget = self.query_one("#usage-models", Static)
        widget.update(models)
        widget.tooltip = "Latest model and effort used. Configuration and routing are in /models."
        if config:
            widget.tooltip += f"\nRB limit: {config.rb.model} / {config.rb.effort}; JB limit: {config.jb.model} / {config.jb.effort}"
        for provider, label in (("openai", "ChatGPT"), ("anthropic", "Claude")):
            row = totals.get(provider, {})
            calls = row.get("calls", 0)
            counts = [
                f"{row.get(kind + '_tokens', 0):,}"
                if row.get(kind + "_reports") or not calls
                else "—"
                for kind in ("input", "output")
            ]
            line = Text(f"{label}\n" if wide else f"{label:<9}", style=TEXT)
            line.append(f"{counts[0]} in · {counts[1]} out", style=MUTED)
            if row.get("incomplete"):
                line.append(" · partial", style=ATTENTION)
            if not wide:
                line.truncate(max(1, self.content_size.width), overflow="ellipsis")
            widget = self.query_one(f"#usage-{provider}", Static)
            widget.update(line)
            widget.tooltip = (
                f"Tokens for this research; input includes cache.\n"
                f"Cache: {row.get('cache_read_tokens', 0):,} read / {row.get('cache_write_tokens', 0):,} written.\n"
                f"{row.get('reported_calls', 0)} / {calls} calls reported usage; last report {age(row.get('reported_at'), now)}."
            )
            quota = self.query_one(f"#limit-{provider}", Static)
            quota_text = (
                quota_details(limits.get(provider, {}), now=now)
                if wide
                else quota_line(limits.get(provider, {}), now=now)
            )
            state = (refresh or {}).get(provider)
            if state:
                quota_text.append(
                    " · " + state, style=ATTENTION if state == "refresh failed" else MUTED
                )
            if not wide:
                quota_text.truncate(max(1, self.content_size.width), overflow="ellipsis")
            quota.update(quota_text)
            quota.tooltip = (
                "Account allowance used. Reset times are reported by the provider.\n"
                + "\n".join(
                    quota_line(
                        {**limits.get(provider, {}), "windows": [window]}, now=now
                    ).plain.strip()
                    for window in limits.get(provider, {}).get("windows", [])
                )
            )


def quota_details(data: dict, *, now=None) -> Text:
    now = time.time() if now is None else now
    if not data.get("windows"):
        return quota_line(data, now=now)
    text = Text(style=MUTED)
    windows = sorted(
        data["windows"],
        key=lambda w: (
            window_label(w) not in {"5h", "7d"},
            window_label(w) != "5h",
            window_label(w),
        ),
    )
    for index, window in enumerate(windows):
        percent = window.get("percent")
        reset = finite(window.get("reset"))
        style = ATTENTION if finite(percent) >= 95 or (reset and reset <= now) else MUTED
        filled = min(10, max(0, int(finite(percent) / 10)))
        bar = "━" * filled + "─" * (10 - filled)
        used = f"{finite(percent):3.0f}%" if percent is not None else "  ? "
        reset_text = duration(reset - now) if reset > now else "refresh due" if reset else ""
        text.append(
            ("\n" if index else "")
            + f"{window_label(window):<10}{bar} {used}"
            + (f" · {reset_text}" if reset_text else ""),
            style=style,
        )
    wait = finite(data.get("until")) - now
    if wait > 0:
        text.append(f"\nWaiting {duration(wait)}", style=ATTENTION)
    text.append(f"\nUpdated {age(data.get('observed'), now)}", style=MUTED)
    return text
