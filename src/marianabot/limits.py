"""Subscription usage snapshots, shared by MB/RB, and persistent cooldowns."""

import math
import time

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
        }
        windows = {w["name"]: w for w in self.data.get("windows", [])}
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
