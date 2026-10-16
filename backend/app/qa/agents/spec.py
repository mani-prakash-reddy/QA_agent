"""AgentSpec: what the supervisor asks a sub-agent to do. Validated in code; the supervisor can only narrow permissions."""

from typing import Literal

from pydantic import BaseModel, Field

AgentKind = Literal[
    "supervisor", "scout", "explorer", "test_author", "mapper", "critic", "extractor"
]

KB_READ = {
    "kb_search_states",
    "kb_get_state",
    "kb_path",
    "kb_role_matrix",
    "kb_get_elements",
}
BRD_READ = {"req_search", "req_get", "req_list"}
BROWSER = {
    "browser_snapshot",
    "browser_click",
    "browser_fill",
    "browser_select",
    "browser_press",
    "browser_navigate",
    "browser_back",
    "browser_wait",
    "probe_validation",
}
RECORDING = {
    "mark_skipped"
}  # states/forms/transitions are recorded automatically on observation
ORCHESTRATION = {
    "spawn_agent",
    "await_agents",
    "get_agent_result",
    "coverage_report",
    "replan",
    "ask_human",
    "finish",
}

# Upper bound of tools per agent kind. A spec's tools must be a subset.
KIND_TOOLS: dict[str, set[str]] = {
    "supervisor": ORCHESTRATION | KB_READ | BRD_READ | {"browser_snapshot"},
    "scout": {"browser_snapshot", "browser_navigate", "browser_back"} | KB_READ,
    "explorer": BROWSER | RECORDING | KB_READ,
    "mapper": KB_READ | BRD_READ,
    "test_author": KB_READ
    | BRD_READ
    | {"gen_field_tests", "gen_role_tests", "submit_test_cases"},
    "critic": KB_READ | {"flag_test_case"},
    "extractor": set(),  # one structured-output call per chunk; no tools
}

MAX_GOAL_CHARS = 2000
MAX_PROMPT_EXTRA_CHARS = 4000


class SpecError(ValueError):
    pass


class Scope(BaseModel):
    role_ref: str | None = None
    module: str | None = None
    url_globs: list[str] = Field(default_factory=list)
    max_depth: int = Field(default=4, ge=0, le=20)


class Resources(BaseModel):
    """References only, never blobs."""

    requirement_ids: list[str] = Field(default_factory=list)
    state_ids: list[str] = Field(default_factory=list)
    sample_ids: list[str] = Field(default_factory=list)


class Budget(BaseModel):
    max_steps: int = Field(default=100, ge=1)
    max_tokens: int = Field(default=200_000, ge=1)
    max_seconds: int = Field(default=900, ge=1)
    max_cost_usd: float | None = Field(default=None, ge=0)


class BudgetCaps(BaseModel):
    """Project-level hard caps; no spec may exceed them."""

    max_steps_per_agent: int = 1000
    max_tokens_per_agent: int = 500_000
    max_seconds_per_agent: int = 1800
    max_cost_usd_per_run: float | None = None
    max_concurrent_agents: int = 3
    max_total_spawns: int = 20


class AgentSpec(BaseModel):
    kind: AgentKind
    goal: str = Field(min_length=1)
    scope: Scope = Field(default_factory=Scope)
    prompt_extra: str = ""
    resources: Resources = Field(default_factory=Resources)
    tools: list[str] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    output_schema: str = "default"


def validate_spec(
    spec: AgentSpec,
    caps: BudgetCaps,
    *,
    known_roles: set[str],
    project_tool_allowlist: set[str] | None = None,
) -> AgentSpec:
    """Return the spec with defaults applied, or raise SpecError. Never widens permissions."""
    allowed = KIND_TOOLS[spec.kind]
    if project_tool_allowlist is not None:
        allowed = allowed & project_tool_allowlist
    tools = spec.tools or sorted(allowed)
    unknown = set(tools) - allowed
    if unknown:
        raise SpecError(
            f"tools not permitted for kind '{spec.kind}': {sorted(unknown)}"
        )
    if len(spec.goal) > MAX_GOAL_CHARS:
        raise SpecError("goal too long")
    if len(spec.prompt_extra) > MAX_PROMPT_EXTRA_CHARS:
        raise SpecError("prompt_extra too long")
    if spec.scope.role_ref is not None and spec.scope.role_ref not in known_roles:
        raise SpecError(f"unknown role_ref '{spec.scope.role_ref}'")
    if spec.kind in {"explorer", "scout"} and spec.scope.role_ref is None:
        raise SpecError("explorer requires scope.role_ref")
    b = spec.budget
    if (
        b.max_steps > caps.max_steps_per_agent
        or b.max_tokens > caps.max_tokens_per_agent
        or b.max_seconds > caps.max_seconds_per_agent
    ):
        raise SpecError("budget exceeds project caps")
    return spec.model_copy(update={"tools": sorted(set(tools))})
