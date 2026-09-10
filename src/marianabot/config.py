import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class BrainConfig(StrictModel):
    model: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9._-]+$")
    agents: int = Field(default=3, ge=2, le=12)
    concurrency: int = Field(default=1, ge=1, le=4)
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"


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


class Config(StrictModel):
    subscription: SubscriptionConfig = Field(default_factory=SubscriptionConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    rb: BrainConfig = Field(default_factory=lambda: BrainConfig(model="gpt-6-astra"))
    jb: BrainConfig = Field(default_factory=lambda: BrainConfig(model="claude-opus-5"))

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.research.min_rounds > self.research.max_rounds:
            raise ValueError("min_rounds must be <= max_rounds")
        return self


def load_config(path: Path) -> Config:
    return Config.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))


DEFAULT_TOML = """# Official clients and subscription logins only. No API billing.
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

[rb]
model = "gpt-6-astra"
agents = 3
concurrency = 1
effort = "high"

[jb]
model = "claude-opus-5"
agents = 3
concurrency = 1
effort = "high"
"""
