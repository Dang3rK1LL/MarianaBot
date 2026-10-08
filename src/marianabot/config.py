import json
import os
import tomllib
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EFFORTS = ("low", "medium", "high", "xhigh", "max")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ModelConfig(StrictModel):
    model: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9._-]+$")
    effort: Literal["auto", "low", "medium", "high", "xhigh", "max"] = "high"

    @model_validator(mode="after")
    def subscription_only(self):
        if "fable" in self.model.lower():
            raise ValueError("Fable requires token billing and is excluded from MarianaBot")
        return self


class BrainConfig(ModelConfig):
    agents: int = Field(default=3, ge=2, le=12)
    concurrency: int = Field(default=1, ge=1, le=4)


class RoutingConfig(StrictModel):
    mode: Literal["adaptive", "fixed"] = "adaptive"


class SubscriptionConfig(StrictModel):
    # Operator attestation, not a remote billing-setting toggle.
    overage_disabled: bool = False
    pause_at_percent: int = Field(default=95, ge=1, le=100)
    unknown_reset_wait_seconds: int = Field(default=1800, ge=60, le=86400)
    request_spacing_seconds: float = Field(default=10, ge=0, le=3600)
    codex_command: str = "codex"
    claude_command: str = "claude"


class ResearchConfig(StrictModel):
    max_rounds: int = Field(default=24, ge=1, le=10000)
    max_hours: float = Field(default=72, gt=0, le=8760)
    min_rounds: int = Field(default=3, ge=1)
    approval_streak: int = Field(default=2, ge=1)
    plateau_rounds: int = Field(default=4, ge=2)
    quality_threshold: int = Field(default=85, ge=0, le=100)
    web_search: bool = True
    max_context_chars: int = Field(default=60000, ge=8000, le=200000)
    request_timeout_seconds: float = Field(default=1800, gt=0, le=7200)
    max_retries: int = Field(default=3, ge=0, le=12)
    max_response_words: int = Field(default=700, ge=150, le=2000)
    memory_chars: int = Field(default=8000, ge=1000, le=32000)


class UpdateConfig(StrictModel):
    enabled: bool = True


class Config(StrictModel):
    updates: UpdateConfig = Field(default_factory=UpdateConfig)
    subscription: SubscriptionConfig = Field(default_factory=SubscriptionConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    mb: ModelConfig = Field(default_factory=lambda: ModelConfig(model="gpt-6-luna", effort="low"))
    rb: BrainConfig = Field(default_factory=lambda: BrainConfig(model="gpt-6-astra"))
    jb: BrainConfig = Field(
        default_factory=lambda: BrainConfig(model="claude-opus-5-5", effort="medium")
    )

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.research.min_rounds > self.research.max_rounds:
            raise ValueError("min_rounds must be <= max_rounds")
        return self


def load_config(path: Path) -> Config:
    return Config.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))


def save_model_preferences(
    path: Path, config: Config, expected_source: str | None, *, overage_disabled: bool | None = None
):
    """Save model choices and optional billing attestation while retaining other settings."""
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current != expected_source:
        raise ValueError("Configuration changed on disk. Reopen /models before saving.")
    config = Config.model_validate(config.model_dump())
    source = current if current is not None else DEFAULT_TOML
    changes = {
        "routing": {"mode": config.routing.mode},
        "mb": {"model": config.mb.model, "effort": config.mb.effort},
        "rb": {"model": config.rb.model, "effort": config.rb.effort},
        "jb": {"model": config.jb.model, "effort": config.jb.effort},
    }
    if overage_disabled is not None:
        changes["subscription"] = {"overage_disabled": overage_disabled}
    lines, section, pending = [], None, {}
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and "]" in stripped:
            lines.extend(f"{key} = {json.dumps(value)}" for key, value in pending.items())
            section = stripped[1 : stripped.index("]")]
            pending = changes.pop(section, {}).copy()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else None
        if key in pending:
            line = f"{key} = {json.dumps(pending.pop(key))}"
        lines.append(line)
    lines.extend(f"{key} = {json.dumps(value)}" for key, value in pending.items())
    for section, values in changes.items():
        lines += ["", f"[{section}]"] + [
            f"{key} = {json.dumps(value)}" for key, value in values.items()
        ]
    output = "\n".join(lines) + "\n"
    Config.model_validate(tomllib.loads(output))
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(output, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


DEFAULT_TOML = """# Official clients and subscription logins only. No API billing.
[updates]
# Check GitHub and install validated application updates at startup when idle.
enabled = true

[subscription]
# Set true ONLY after disabling extra usage and automatic credit purchases
# in both accounts. MarianaBot cannot change or verify these billing settings.
overage_disabled = false
pause_at_percent = 95
unknown_reset_wait_seconds = 1800
request_spacing_seconds = 10
codex_command = "codex"
claude_command = "claude"

[research]
max_rounds = 24
min_rounds = 3
max_hours = 72
approval_streak = 2
plateau_rounds = 4
quality_threshold = 85
web_search = true
max_context_chars = 60000
request_timeout_seconds = 1800
max_retries = 3
max_response_words = 700
memory_chars = 8000

[routing]
# Route routine work to efficient subscription models; RB/JB choices are ceilings.
mode = "adaptive"

[mb]
model = "gpt-6-luna"
effort = "low"

[rb]
model = "gpt-6-astra"
agents = 3
concurrency = 1
effort = "high"

[jb]
model = "claude-opus-5-5"
agents = 3
concurrency = 1
effort = "medium"
"""
