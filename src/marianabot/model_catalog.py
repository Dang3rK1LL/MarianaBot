"""Provider model choices and supported ordinary reasoning effort levels."""

import re
from dataclasses import dataclass
from pathlib import Path

from marianabot.clients import ClientError, CodexAccount, claude_models
from marianabot.config import EFFORTS, Config


@dataclass(frozen=True)
class ModelOption:
    model: str
    label: str
    efforts: tuple[str, ...]
    default_effort: str = "medium"


def parse_models(provider: str, entries: list[dict]) -> list[ModelOption]:
    options = {}
    for entry in entries:
        if entry.get("hidden") or entry.get("available") is False:
            continue
        if provider == "rb":
            model = entry.get("model") or entry.get("id")
            reported = [
                item.get("reasoningEffort") for item in entry.get("supportedReasoningEfforts", [])
            ]
            default = entry.get("defaultReasoningEffort")
            if not reported and default in EFFORTS:
                reported = [default]
        else:
            model = entry.get("resolvedModel") or entry.get("value")
            reported = entry.get("supportedEffortLevels", [])
            if entry.get("supportsEffort") and not reported:
                continue
            default = entry.get("defaultEffortLevel", "medium")
        if not isinstance(model, str) or not re.fullmatch(r"[a-zA-Z0-9._-]{1,100}", model):
            continue
        efforts = tuple(level for level in EFFORTS if level in reported)
        if reported and not efforts:
            continue
        if not efforts:
            default = "auto"
        elif default not in efforts:
            default = efforts[0]
        label = entry.get("displayName") or model
        description = entry.get("description") or ""
        if provider == "jb" and " · " in description:
            label = description.split(" · ", 1)[0]
        if provider == "jb" and entry.get("value") == "default":
            label = model
        option = ModelOption(model, label, efforts, default)
        if model not in options or entry.get("value") != "default":
            options[model] = option
    return list(options.values())


async def load_models(provider: str, config: Config, cwd: Path) -> list[ModelOption]:
    cwd.mkdir(parents=True, exist_ok=True)
    if provider == "rb":
        account = await CodexAccount(config, cwd).snapshot(include_models=True)
        entries = account["model_details"]
    else:
        entries = await claude_models(config, cwd)
    models = parse_models(provider, entries)
    if not models:
        raise ClientError("The provider did not report any compatible models")
    return models
