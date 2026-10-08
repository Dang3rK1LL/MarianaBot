"""Task routing over subscription CLI catalogs; no additional inference for routing."""

import re
from dataclasses import dataclass

from marianabot.config import EFFORTS, Config
from marianabot.model_catalog import ModelOption, subscription_eligible


@dataclass(frozen=True)
class Selection:
    model: str
    effort: str
    reason: str


def phase_for(history: list[dict], revision: int) -> str:
    current = [row for row in history if row["revision"] == revision]
    if current:
        last = current[-1]["review"]
        if not last.get("foundation_ready") or not all(
            last.get(key, True)
            for key in ("scope_aligned", "owner_constraints_preserved", "online_only")
        ):
            return "foundation"
    ready = [i for i, row in enumerate(current) if row["review"].get("foundation_ready")]
    if not ready:
        return "foundation"
    return "validation" if ready[0] == len(current) - 1 else "decision"


class TaskRouter:
    def __init__(self, config: Config):
        self.config = config
        self.catalogs: dict[str, dict[str, ModelOption]] = {}

    def set_catalog(self, provider: str, models: list[ModelOption]):
        self.catalogs[provider] = {
            option.model: option for option in models if subscription_eligible(option.model)
        }

    def family(self, provider: str, family: str, fallback: str) -> str:
        models = [model for model in self.catalogs.get(provider, {}) if family in model.lower()]
        # Prefer the newest reported version, never an invented model identifier.
        return (
            max(models, key=lambda model: tuple(map(int, re.findall(r"\d+", model))))
            if models
            else fallback
        )

    def select(
        self, brain: str, task: str, *, phase="foundation", difficult=False, retry=0
    ) -> Selection:
        provider = "jb" if brain == "JB" else "rb"
        ceiling = self.config.jb if brain == "JB" else self.config.rb
        if brain == "MB":
            model, desired, reason = (
                self.config.mb.model,
                self.config.mb.effort,
                "brief or owner message",
            )
            if task.startswith("memory-"):
                desired, reason = "medium", "bounded memory summary"
            if retry:
                model, desired, reason = (
                    self.family("rb", "sol", ceiling.model),
                    "medium",
                    "summary or response validation retry",
                )
        elif self.config.routing.mode == "fixed":
            model, desired, reason = ceiling.model, ceiling.effort, "fixed selection"
        elif difficult or retry or phase == "decision":
            model, desired, reason = (
                ceiling.model,
                "high",
                "decision, persistent blocker or validation retry",
            )
        else:
            family = "sonnet" if brain == "JB" else "sol"
            # A lightweight ceiling must not silently become a larger model.
            lightweight = any(name in ceiling.model.lower() for name in ("luna", "haiku"))
            model = ceiling.model if lightweight else self.family(provider, family, ceiling.model)
            desired, reason = "medium", f"{phase}: evidence and scoped analysis"
        catalog = self.catalogs.get(provider, {})
        if catalog and model not in catalog:
            if self.config.routing.mode == "fixed" and brain != "MB":
                raise ValueError("The fixed model is unavailable; no substitute was selected")
            model = self.family(
                provider,
                "luna" if brain == "MB" else "sonnet" if brain == "JB" else "sol",
                ceiling.model,
            )
        if not subscription_eligible(model) or (catalog and model not in catalog):
            raise ValueError("No eligible subscription model is available for this task")
        option = catalog.get(model)
        # Live dispatch has provider metadata. Offline fixtures need only the
        # configured ceiling; they never launch a provider or assert entitlement.
        levels = option.efforts if option else (() if desired == "auto" else EFFORTS)
        if not levels:
            effort = "auto"
        elif desired == "auto":
            effort = option.default_effort if option else "auto"
        else:
            maximum = (
                ceiling.effort
                if brain != "MB"
                else "medium"
                if retry or task.startswith("memory-")
                else self.config.mb.effort
            )
            cap = EFFORTS.index(maximum) if maximum in EFFORTS else EFFORTS.index(desired)
            rank = min(EFFORTS.index(desired), cap)
            eligible = [level for level in levels if EFFORTS.index(level) <= rank]
            if not eligible:
                raise ValueError("No reported effort level fits the configured ceiling")
            effort = eligible[-1]
        return Selection(model, effort, reason)
