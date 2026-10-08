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
            yield Static("Research & usage", id="usage-scope", markup=False)
            yield Button("Refresh", id="refresh-usage")
        yield Static("", id="research-overview", markup=False)
        yield Static("", id="routing-summary", markup=False)
        yield Static("", id="usage-models", markup=False)
        yield Static("", id="usage-openai", markup=False)
        yield Static("", id="limit-openai", markup=False)
        yield Static("", id="usage-anthropic", markup=False)
        yield Static("", id="limit-anthropic", markup=False)
        yield Static("", id="usage-notes", markup=False)

    def update_usage(
        self,
        totals: dict,
        limits: dict,
        *,
        scope: str,
        config: Config | None,
        demo: bool,
        working: bool,
        now=None,
        refresh: dict | None = None,
        selections: dict | None = None,
        run: dict | None = None,
        memory: dict | None = None,
    ):
        now = time.time() if now is None else now
        wide = self.app.size.width >= 120
        self.query_one("#usage-scope", Static).update(scope + " · usage")
        overview = "No active research\nChoose an objective and a work folder to begin."
        if run:
            overview = f"{run['id']}\n{run['status'].capitalize()} · round {run['round']}\nElapsed {duration(now - run['created'])} · includes waits"
            if run.get("reason"):
                overview += "\n" + run["reason"]
        self.query_one("#research-overview", Static).update(overview)
        route = (
            "Adaptive routing"
            if config and config.routing.mode == "adaptive"
            else "Fixed research models"
        )
        route += " · online validation only"
        if config:
            route += f"\nRB ceiling: {config.rb.model} / {config.rb.effort}\nJB ceiling: {config.jb.model} / {config.jb.effort}"
        self.query_one("#routing-summary", Static).update(route)
        models = Text(style=MUTED)
        if config is None:
            models.append("Models unavailable · check /models", style=ATTENTION)
        else:
            models.append("Current / last selected models", style=MUTED)
            for label, brain in (("MB", config.mb), ("RB", config.rb), ("JB", config.jb)):
                selected = (selections or {}).get(label)
                model = selected["model"] if selected else brain.model
                effort = (
                    selected.get("effort") or "unknown (older call)" if selected else brain.effort
                )
                models.append(
                    f"\n{label:<3}{model} / {'default' if effort == 'auto' else effort}", style=TEXT
                )
                if not selected and config.routing.mode == "adaptive" and label != "MB":
                    models.append(" (ceiling)", style=MUTED)
        model_widget = self.query_one("#usage-models", Static)
        model_widget.update(models)
        model_widget.tooltip = models.plain
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
            line = Text(f"{label}\n" if wide else f"{label:<14}", style=TEXT)
            line.append(f" {incoming} in  /  {outgoing} out", style=TEXT)
            if count:
                state = f"{count} active"
                if row.get("incomplete"):
                    state += " · partial"
            elif row.get("incomplete"):
                state = "partial totals"
            else:
                state = "idle" if working else "saved" if calls else "idle"
            line.append(f"\n{state}" if wide else f"  ·  {state}", style=MUTED)
            if wide:
                line.append(
                    f"\nRun calls {calls:,} · usage reported {row.get('reported_calls', 0):,}",
                    style=MUTED,
                )
                line.append(
                    f"\nCache {row.get('cache_read_tokens', 0):,} read / {row.get('cache_write_tokens', 0):,} write",
                    style=MUTED,
                )
                line.append(f"\nToken report {age(row.get('reported_at'), now)}", style=MUTED)
            else:
                line.truncate(max(1, self.content_size.width), overflow="ellipsis")
            widget = self.query_one(f"#usage-{provider}", Static)
            widget.update(line)
            widget.tooltip = (
                f"Reported input: {incoming}; output: {outgoing}. Input includes cache.\n"
                f"Cache read: {row.get('cache_read_tokens', 0):,}; cache write: {row.get('cache_write_tokens', 0):,}.\n"
                f"{row.get('reported_calls', 0)} of {calls} calls have usage reports. Last report: {age(row.get('reported_at'), now)}.\n"
                "Counts belong to this MarianaBot run, not other apps using your subscriptions."
            )
            quota = self.query_one(f"#limit-{provider}", Static)
            quota_text = (
                quota_details(limits.get(provider, {}), demo=demo, now=now)
                if wide
                else quota_line(limits.get(provider, {}), demo=demo, now=now)
            )
            state = (refresh or {}).get(provider)
            if state and not demo:
                quota_text.append(
                    " · " + state,
                    style=ATTENTION if state == "refresh failed" else MUTED,
                )
            if not wide:
                quota_text.truncate(max(1, self.content_size.width), overflow="ellipsis")
            quota.update(quota_text)
            quota.tooltip = "Allowance percentages are used, not remaining.\n" + "\n".join(
                quota_line({**limits.get(provider, {}), "windows": [window]}, now=now).plain.strip()
                for window in limits.get(provider, {}).get("windows", [])
            )
        notes = "Tokens are for this run; input includes cache.\nAllowance is account-wide and shown as used."
        if memory:
            notes += (
                f"\nMemory: {len(memory['text']):,} chars · through round {memory['through_round']}"
            )
        notes += "\nFeedback stays verbatim; full work is archived.\n/models settings · /usage full report · /memory"
        self.query_one("#usage-notes", Static).update(notes)


def quota_details(data: dict, *, demo=False, now=None) -> Text:
    now = time.time() if now is None else now
    if demo or not data.get("windows"):
        return quota_line(data, demo=demo, now=now)
    text = Text("Account allowance used", style=MUTED)
    windows = sorted(
        data["windows"],
        key=lambda w: (
            window_label(w) not in {"5h", "7d"},
            window_label(w) != "5h",
            window_label(w),
        ),
    )
    for window in windows:
        percent = window.get("percent")
        label = window_label(window)
        reset = finite(window.get("reset"))
        style = ATTENTION if finite(percent) >= 95 or (reset and reset <= now) else MUTED
        if percent is None:
            bar, used = "·" * 12, str(window.get("status", "unknown"))
        else:
            filled = min(12, max(0, int(finite(percent) * 12 / 100)))
            bar, used = "━" * filled + "─" * (12 - filled), f"{finite(percent):3.0f}%"
        text.append(f"\n{label:<10}{bar} {used}", style=style)
        text.append(
            "\n"
            + (
                f"  Reset in {duration(reset - now):<10}"
                if reset > now
                else "  Reset passed · refresh due"
                if reset
                else "  Reset not reported"
            ),
            style=style,
        )
    wait = finite(data.get("until")) - now
    if wait > 0:
        text.append(f"\nWaiting {duration(wait)}", style=ATTENTION)
    text.append(f"\nLimits checked {age(data.get('observed'), now)}", style=MUTED)
    return text
