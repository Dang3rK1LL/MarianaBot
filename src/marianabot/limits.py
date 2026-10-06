"""Subscription usage snapshots, shared by MB/RB, and persistent cooldowns."""

import math
import time
from datetime import UTC, datetime

from marianabot.config import SubscriptionConfig
from marianabot.store import Store


def finite(value, default=0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (ValueError, TypeError):
        return default


class SubscriptionLimits:
    def __init__(self, store: Store, provider: str, config: SubscriptionConfig):
        self.store, self.provider, self.config = store, provider, config
        self.data = store.get_limits(provider)

    def save(self):
        self.store.set_limits(self.provider, self.data)

    def codex(self, payload: dict, now: float | None = None):
        now = time.time() if now is None else now
        buckets = list((payload.get("rateLimitsByLimitId") or {}).values())
        if not buckets and payload.get("rateLimits"):
            buckets = [payload["rateLimits"]]
        if not buckets:
            raise ValueError("Codex returned no subscription usage windows")
        windows = []
        until = 0
        for bucket in buckets:
            for name in ("primary", "secondary"):
                window = bucket.get(name)
                if not window:
                    continue
                used = finite(window.get("usedPercent"), 100)
                reset = finite(window.get("resetsAt"))
                windows.append(
                    {
                        "name": f"{bucket.get('limitId', 'codex')} {name}",
                        "percent": used,
                        "reset": reset,
                        "duration_minutes": finite(window.get("windowDurationMins")),
                        "observed": now,
                    }
                )
                if used >= self.config.pause_at_percent:
                    until = max(
                        until,
                        reset if reset > now else now + self.config.unknown_reset_wait_seconds,
                    )
            if bucket.get("rateLimitReachedType"):
                until = max(
                    until,
                    max(
                        (w["reset"] for w in windows if w["reset"] > now),
                        default=now + self.config.unknown_reset_wait_seconds,
                    ),
                )
        if not windows:
            raise ValueError("Codex returned no readable usage windows")
        self.data.update(windows=windows, observed=now, source="Codex account/rateLimits/read")
        self.data["until"] = max(self.data.get("until", 0), until)
        self.save()

    def claude_snapshot(self, payload: dict, now: float | None = None, *, observed=None, model=""):
        """Claude's account percentages use 0..100; stream events use fractions."""
        now = time.time() if now is None else now
        observed = now if observed is None else observed
        windows = []
        until = 0

        def add(name, info, *, applies=True):
            nonlocal until
            if not isinstance(info, dict):
                return
            percent = finite(info.get("utilization"), None)
            if percent is None or percent < 0 or percent > 100:
                return
            reset = info.get("resets_at")
            if isinstance(reset, str):
                try:
                    parsed = datetime.fromisoformat(reset.replace("Z", "+00:00"))
                    reset = parsed.replace(tzinfo=parsed.tzinfo or UTC).timestamp()
                except ValueError:
                    reset = 0
            reset = finite(reset)
            windows.append(dict(name=name, percent=percent, reset=reset, observed=observed))
            if applies and percent >= self.config.pause_at_percent:
                until = max(
                    until, reset if reset > now else now + self.config.unknown_reset_wait_seconds
                )

        for name in (
            "five_hour",
            "seven_day",
            "seven_day_opus",
            "seven_day_sonnet",
            "seven_day_oauth_apps",
        ):
            applies = name in {"five_hour", "seven_day", "seven_day_oauth_apps"} or (
                name.removeprefix("seven_day_") in model.lower()
            )
            add(name, payload.get(name), applies=applies)
        for info in payload.get("model_scoped") or []:
            if isinstance(info, dict) and info.get("display_name"):
                name = str(info["display_name"])
                add(name + " 7d", info, applies=name.lower().split()[0] in model.lower())
        if not windows:
            raise ValueError("Claude returned no readable subscription usage windows")
        self.data = self.store.get_limits(self.provider)
        prior = {w["name"]: w for w in self.data.get("windows", [])}
        windows = [
            prior[w["name"]] if finite(prior.get(w["name"], {}).get("observed")) > observed else w
            for w in windows
        ]
        self.data.update(windows=windows, observed=observed, source="Claude get_usage")
        self.data["until"] = max(self.data.get("until", 0), until)
        self.save()

    def claude(self, info: dict, now: float | None = None):
        now = time.time() if now is None else now
        reset = finite(info.get("resetsAt"))
        utilization = info.get("utilization")
        percent = finite(utilization, 1) * 100 if utilization is not None else None
        window = {
            "name": info.get("rateLimitType", "Claude reported window"),
            "percent": percent,
            "reset": reset,
            "status": info.get("status", "unknown"),
            "observed": now,
        }
        self.data = self.store.get_limits(self.provider)
        windows = {w["name"]: w for w in self.data.get("windows", [])}
        prior = windows.get(window["name"])
        if percent is None and prior and prior.get("percent") is not None:
            # An 'allowed' event often omits utilization. Keep the actual measurement
            # and its age instead of erasing it or calling an old percentage fresh.
            window = prior | {"status": window["status"]}
        windows[window["name"]] = window
        self.data.update(
            windows=list(windows.values()), observed=now, source="Claude rate_limit_event"
        )
        if info.get("status") == "rejected" or (
            percent is not None and percent >= self.config.pause_at_percent
        ):
            self.defer(reset if reset > now else now + self.config.unknown_reset_wait_seconds)
        else:
            self.save()

    def defer(self, until: float):
        self.data["until"] = max(self.data.get("until", 0), until)
        self.save()

    def remaining(self, now: float | None = None) -> float:
        return max(0, self.data.get("until", 0) - (time.time() if now is None else now))
