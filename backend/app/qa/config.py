"""Per-project configuration (stored as JSONB, snapshotted into each run)."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

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
    explorer_mode: Literal["agentic", "deterministic"] = "deterministic"
    max_concurrent_agents: int = Field(default=3, ge=1, le=10)
    # After the crawl, open (by address, read-only) the screens other roles reached and this role did not.
    probe_forbidden_routes: bool = True
    # Walk multi-step forms: fill each step with valid sample values and press Next / Continue (never the
    # final Create / Save / Submit). Off by default: a step of some applications saves a draft.
    walk_wizards: bool = False
    # agentic mode: how many modules to plan, steps per module agent, and replans driven by the coverage report
    max_modules: int = Field(default=12, ge=1, le=50)
    scout_max_pages: int = Field(
        default=25, ge=1, le=100
    )  # pages a scout opens per role
    max_agents_per_package: int = Field(default=3, ge=1, le=6)
    max_sessions_per_role: int = Field(
        default=3, ge=1, le=6
    )  # parallel logins of one role
    agent_steps_per_module: int = Field(default=40, ge=5, le=300)
    agentic_replans: int = Field(default=1, ge=0, le=3)
    explorer_model: Literal["fast", "strong"] = "strong"
    # Record masked table headers and fixed captions (never table data) so requirements about what a
    # screen shows can be mapped to it. Off = controls, forms and headings only.
    capture_static_text: bool = True
    max_access_probes: int = Field(default=40, ge=0, le=500)
    screenshots: Literal["all", "new_states", "off"] = "new_states"
    seeds: list[str] = Field(default_factory=list)
    stale_after_days: int = 14


TEMPLATE_COLUMNS: dict[str, str] = {
    "tc_key": "Test Case ID",
    "req_key": "Requirement ID",
    "module": "Module",
    "title": "Title",
    "preconditions": "Preconditions",
    "steps": "Test Steps",
    "test_data": "Test Data",
    "expected_result": "Expected Result",
    "priority": "Priority",
    "test_type": "Test Type",
    "role_name": "Role",
    "status": "Status",
    "origin": "Origin",
    "expectation_basis": "Expected result basis",
}
DEFAULT_COLUMNS = [
    "tc_key", "req_key", "module", "title", "preconditions", "steps", "test_data",
    "expected_result", "priority", "test_type", "role_name",
]  # fmt: skip


class TemplateColumn(BaseModel):
    key: str
    header: str = ""

    @model_validator(mode="after")
    def _known(self) -> TemplateColumn:
        if self.key not in TEMPLATE_COLUMNS:
            raise ValueError(f"unknown column '{self.key}'")
        self.header = self.header.strip() or TEMPLATE_COLUMNS[self.key]
        return self


class TestCaseTemplate(BaseModel):
    """Which columns the exports have, in what order, under what names (FR-E5)."""

    __test__ = False
    columns: list[TemplateColumn] = Field(
        default_factory=lambda: [TemplateColumn(key=k) for k in DEFAULT_COLUMNS],
        min_length=1,
    )
    id_prefix: str = Field(default="TC", pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,9}$")


class GenerationConfig(BaseModel):
    test_types: list[TestType] = Field(default_factory=lambda: _all_test_types())
    max_cases_per_requirement: int = Field(default=12, ge=1)
    # Cases written by the author agent per requirement (business rules and flows).
    llm_cases_per_requirement: int = Field(default=4, ge=0, le=12)
    # Mechanical cases (field validation, boundaries) kept per form, highest priority first.
    max_rule_cases_per_form: int = Field(default=30, ge=1)
    map_confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    template: TestCaseTemplate = Field(default_factory=TestCaseTemplate)
    dedup_cosine: float = Field(default=0.92, ge=0, le=1)
    include_flagged_requirements: bool = False
    models: dict[str, str] = Field(
        default_factory=dict
    )  # agent kind -> model id override
