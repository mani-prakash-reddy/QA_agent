"""Per-project configuration (stored as JSONB, snapshotted into each run)."""

from typing import Literal

from pydantic import BaseModel, Field

from app.qa.agents.spec import BudgetCaps

__all__ = ["BudgetCaps", "CrawlConfig", "GenerationConfig"]

TestType = Literal[
    "positive",
    "negative",
    "boundary",
    "field_validation",
    "role_access",
    "e2e",
    "business_rule",
]


def _all_test_types() -> list[TestType]:
    return [
        "positive",
        "negative",
        "boundary",
        "field_validation",
        "role_access",
        "e2e",
        "business_rule",
    ]


# App-shell controls that change chrome state (persisted by the app), not application function.
# They are never clicked and never part of a screen's identity.
DEFAULT_SHELL_TOGGLES = [
    r"sidebar",
    r"appearance",
    r"toggle theme",
    r"dark mode",
    r"light mode",
]


class CrawlConfig(BaseModel):
    max_depth: int = Field(default=4, ge=0, le=20)
    max_states: int = Field(default=300, ge=1)
    max_steps_per_agent: int = Field(default=300, ge=1)
    include_globs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)
    include_modules: list[str] = Field(default_factory=list)
    exclude_modules: list[str] = Field(default_factory=list)
    allow_destructive: bool = False
    destructive_allowlist: list[str] = Field(default_factory=list)
    safe_post_patterns: list[str] = Field(default_factory=list)
    ignore_selectors: list[str] = Field(default_factory=list)  # dev overlays etc.
    shell_toggle_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SHELL_TOGGLES)
    )
    # "agentic" arrives in M4; until then every run uses the deterministic explorer.
    explorer_mode: Literal["agentic", "deterministic"] = "deterministic"
    max_concurrent_agents: int = Field(default=3, ge=1, le=10)
    screenshots: Literal["all", "new_states", "off"] = "new_states"
    seeds: list[str] = Field(default_factory=list)
    stale_after_days: int = 14


class GenerationConfig(BaseModel):
    test_types: list[TestType] = Field(default_factory=lambda: _all_test_types())
    max_cases_per_requirement: int = Field(default=12, ge=1)
    map_confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    dedup_cosine: float = Field(default=0.92, ge=0, le=1)
    include_flagged_requirements: bool = False
    models: dict[str, str] = Field(
        default_factory=dict
    )  # agent kind -> model id override
