from pathlib import Path
from typing import Literal
import tomllib

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class BrainConfig(StrictModel):
    model: str
    agents: int = Field(default=3, ge=2, le=12)
    concurrency: int = Field(default=2, ge=1, le=8)
    max_output_tokens: int = Field(default=8192, ge=512, le=128000)
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    input_usd_per_million: float = Field(gt=0)
    output_usd_per_million: float = Field(gt=0)
    # Conservative prices used for reservations and accounting, including long context/cache writes.
    context_tokens: int = Field(default=1050000, ge=8192)
    requests_per_minute: int = Field(default=10, ge=1)
    tokens_per_minute: int = Field(default=100000, ge=1000)


class BudgetConfig(StrictModel):
    daily_usd: float = Field(default=0, ge=0)
    run_usd: float = Field(default=0, ge=0)


class ResearchConfig(StrictModel):
    max_rounds: int = Field(default=24, ge=1, le=10000)
    max_hours: float = Field(default=72, gt=0, le=8760)
    min_rounds: int = Field(default=3, ge=1)
    approval_streak: int = Field(default=2, ge=1)
    plateau_rounds: int = Field(default=4, ge=2)
    quality_threshold: int = Field(default=85, ge=0, le=100)
    web_search: bool = True
    max_search_calls: int = Field(default=2, ge=1, le=10)
    search_usd_per_call: float = Field(default=0.01, gt=0)
    max_context_chars: int = Field(default=60000, ge=8000, le=200000)
    request_timeout_seconds: float = Field(default=900, gt=0, le=3600)
    max_retries: int = Field(default=4, ge=0, le=12)


class Config(StrictModel):
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    rb: BrainConfig = Field(default_factory=lambda: BrainConfig(
        model="gpt-6-astra", input_usd_per_million=25, output_usd_per_million=75))
    jb: BrainConfig = Field(default_factory=lambda: BrainConfig(
        model="claude-opus-5", input_usd_per_million=5, output_usd_per_million=25,
        context_tokens=1000000))

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.research.min_rounds > self.research.max_rounds:
            raise ValueError("min_rounds must be <= max_rounds")
        for brain in (self.rb, self.jb):
            if brain.max_output_tokens >= brain.context_tokens:
                raise ValueError("Output allowance must be smaller than the model context")
        return self


def load_config(path: Path) -> Config:
    return Config.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))


DEFAULT_TOML = '''# MarianaBot configuration. Prices checked 2026-09-10; recheck before live use.
# Zero budgets deliberately disable paid runs. Set BOTH before starting.
[budget]
daily_usd = 0.0
run_usd = 0.0

[research]
max_rounds = 24
min_rounds = 3
max_hours = 72
approval_streak = 2
plateau_rounds = 4
quality_threshold = 85
web_search = true
max_search_calls = 2
search_usd_per_call = 0.01
max_context_chars = 60000
request_timeout_seconds = 900
max_retries = 4

[rb]
model = "gpt-6-astra"
agents = 3
concurrency = 2
max_output_tokens = 8192
effort = "high"
# Conservative ceilings: long-context pricing plus possible cache writes.
# Accounting deliberately overestimates invoices; MB shares these settings.
input_usd_per_million = 25.0
output_usd_per_million = 75.0
context_tokens = 1050000
requests_per_minute = 10
tokens_per_minute = 100000

[jb]
model = "claude-opus-5"
agents = 3
concurrency = 2
max_output_tokens = 8192
effort = "high"
input_usd_per_million = 5.0
output_usd_per_million = 25.0
context_tokens = 1000000
requests_per_minute = 10
tokens_per_minute = 100000
'''
