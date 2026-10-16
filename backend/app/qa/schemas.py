import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from app.qa.config import BudgetCaps, CrawlConfig, GenerationConfig
from app.qa.security.urls import TargetUrlError, validate_target_url


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    target_base_url: str
    # The target must be a test/staging environment; the project dialog states it, so it defaults to true.
    environment_ack: bool = True
    confidential: bool = False
    crawl_config: CrawlConfig = Field(default_factory=CrawlConfig)
    generation_config: GenerationConfig = Field(default_factory=GenerationConfig)
    budget_caps: BudgetCaps = Field(default_factory=BudgetCaps)

    @field_validator("target_base_url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        try:
            return validate_target_url(v)
        except TargetUrlError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("environment_ack")
    @classmethod
    def _check_ack(cls, v: bool) -> bool:
        if not v:
            raise ValueError(
                "confirm that the target is a test/staging environment, never production"
            )
        return v


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    target_base_url: str | None = None
    confidential: bool | None = None
    crawl_config: CrawlConfig | None = None
    generation_config: GenerationConfig | None = None
    budget_caps: BudgetCaps | None = None

    @field_validator("target_base_url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            return validate_target_url(v)
        except TargetUrlError as exc:
            raise ValueError(str(exc)) from exc


class ProjectPublic(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    target_base_url: str
    environment_ack: bool
    confidential: bool
    crawl_config: dict[str, Any]
    generation_config: dict[str, Any]
    budget_caps: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=500)
    password: SecretStr = SecretStr("")
    totp_seed: SecretStr | None = None
    # A browser session exported from a real login (Playwright storage state, JSON). The way past
    # CAPTCHA and single sign-on; used instead of the password when login_recipe.mfa is "storage_state".
    storage_state: SecretStr | None = None


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    # login url / selector hints / mfa type ("none" | "totp" | "otp_human" | "bypass" | "storage_state")
    login_recipe: dict[str, Any] = Field(default_factory=dict)
    credentials: Credentials | None = None

    @model_validator(mode="after")
    def _no_secrets_in_recipe(self) -> RoleCreate:
        forbidden = {"password", "passwd", "secret", "totp_seed", "token", "api_key"}
        if any(k.lower() in forbidden for k in self.login_recipe):
            raise ValueError(
                "login_recipe must not contain secrets; send them in 'credentials'"
            )
        return self


class RolePublic(BaseModel):
    """Never includes credential material, only whether it is set."""

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None
    login_recipe: dict[str, Any]
    has_credentials: bool


RunKind = Literal["echo", "crawl", "ingest", "map", "generate", "regenerate", "full"]


class RunCreate(BaseModel):
    kind: RunKind
    params: dict[str, Any] = Field(default_factory=dict)


class HumanInput(BaseModel):
    role: str = Field(min_length=1, max_length=100)
    value: SecretStr = Field(min_length=1, max_length=200)


class RunPublic(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    kind: str
    status: str
    config_snapshot: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    summary: dict[str, Any]
    created_at: datetime


class EventPublic(BaseModel):
    seq: int
    agent_id: str | None
    ts: datetime
    type: str
    payload: dict[str, Any]


class AgentPublic(BaseModel):
    id: uuid.UUID
    parent_agent_id: uuid.UUID | None
    kind: str
    status: str
    spec: dict[str, Any]
    result: dict[str, Any] | None
    steps: int
    started_at: datetime
    finished_at: datetime | None


class ControlRequest(BaseModel):
    action: Literal["pause", "resume", "stop"]
    agent_id: str | None = None


# --- knowledge model (read API) ---------------------------------------------------------


class SnapshotPublic(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID | None
    status: str
    roles_covered: list[Any]
    stats: dict[str, Any]
    created_at: datetime
    stale_after: datetime | None


class StatePublic(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    module: str | None
    route_template: str | None
    sample_url: str | None
    depth: int
    parent_state_id: uuid.UUID | None
    nav_path: list[Any]
    has_screenshot: bool
    roles: list[str]
    n_elements: int
    n_forms: int
    discovery_source: str


class ElementPublic(BaseModel):
    id: uuid.UUID
    key: str
    kind: str
    label: str
    region: str
    in_row: bool
    attrs: dict[str, Any]
    is_submit: bool
    is_destructive: bool
    destructive_reason: str | None


class FieldPublic(BaseModel):
    id: uuid.UUID
    ordinal: int
    label: str
    input_type: str
    required: bool
    min_length: int | None
    max_length: int | None
    min_value: str | None
    max_value: str | None
    pattern: str | None
    options: dict[str, Any] | None
    validation_messages: list[Any]
    default_present: bool


class FormPublic(BaseModel):
    id: uuid.UUID
    name: str
    submit_label: str | None
    fields: list[FieldPublic]


class TransitionPublic(BaseModel):
    id: uuid.UUID
    from_state_id: uuid.UUID
    from_title: str
    to_state_id: uuid.UUID
    to_title: str
    via_label: str
    kind: str


class StateDetail(BaseModel):
    id: uuid.UUID
    snapshot_id: uuid.UUID
    kind: str
    title: str
    heading: str | None
    module: str | None
    route_template: str | None
    sample_url: str | None
    depth: int
    nav_path: list[Any]
    parent_state_id: uuid.UUID | None
    has_screenshot: bool
    roles: list[str]
    elements: list[ElementPublic]
    forms: list[FormPublic]
    outgoing: list[TransitionPublic]
    incoming: list[TransitionPublic]


class MatrixRow(BaseModel):
    state_id: uuid.UUID
    title: str
    kind: str
    module: str | None
    route: str | None
    access: dict[str, bool]
    # what a role got when it opened the screen by address: forbidden | not_found | redirected
    denied: dict[str, str] = Field(default_factory=dict)


class MatrixPublic(BaseModel):
    roles: list[str]
    rows: list[MatrixRow]


class CrawlEventPublic(BaseModel):
    id: int
    ts: datetime
    role: str | None
    url: str | None
    action: str | None
    element_label: str | None
    status: str
    reason: str | None
    source: str | None


class CrawlLogPublic(BaseModel):
    summary: list[dict[str, Any]]
    events: list[CrawlEventPublic]


# --- BRD documents and requirements -------------------------------------------------------


class BrdPublic(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    mime: str
    status: str
    page_count: int | None
    chunk_count: int
    requirement_count: int
    error: str | None
    run_id: uuid.UUID | None
    created_at: datetime


class BrdUploadResult(BaseModel):
    document: BrdPublic
    run_id: uuid.UUID


class ChunkPublic(BaseModel):
    id: uuid.UUID
    ordinal: int
    section_path: str
    page_from: int | None
    page_to: int | None
    kind: str
    text: str


RequirementType = Literal[
    "functional", "business_rule", "validation", "acceptance_criterion", "nfr"
]
ReviewStatus = Literal["extracted", "approved", "rejected", "edited"]


class RequirementPublic(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    document_id: uuid.UUID | None
    req_key: str
    key_origin: str
    type: str
    title: str
    description: str
    actors: list[Any]
    module: str | None
    acceptance_criteria: list[Any]
    source_anchor: dict[str, Any]
    ambiguity_flag: bool
    ambiguity_reasons: list[Any]
    ambiguity_resolved: bool
    needs_decision: bool  # flagged and nobody has decided yet
    review_status: str
    version: int
    has_embedding: bool
    created_at: datetime
    updated_at: datetime


class RequirementsPage(BaseModel):
    data: list[RequirementPublic]
    count: int


class RequirementDetail(RequirementPublic):
    source_text: str | None  # the full chunk the quote came from
    source_page_to: int | None


class RequirementCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1)
    type: RequirementType = "functional"
    req_key: str | None = Field(default=None, max_length=100)
    module: str | None = Field(default=None, max_length=200)
    actors: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class RequirementUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, min_length=1)
    type: RequirementType | None = None
    module: str | None = Field(default=None, max_length=200)
    actors: list[str] | None = None
    acceptance_criteria: list[str] | None = None


class BulkAction(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    action: Literal["approve", "reject"]


class BulkResult(BaseModel):
    updated: int
    skipped: list[dict[str, str]]  # {"req_key", "reason"}


class RequirementsSummary(BaseModel):
    total: int
    by_status: dict[str, int]
    by_type: dict[str, int]
    needs_decision: int
    approved: int


# ---------------------------------------------------------------------------- mapping and gaps
class MappingRunRequest(BaseModel):
    snapshot_id: uuid.UUID | None = None  # default: the latest finished crawl
    threshold: float | None = Field(default=None, ge=0.1, le=0.95)


class MappingPublic(BaseModel):
    id: uuid.UUID
    requirement_id: uuid.UUID
    req_key: str
    requirement_title: str
    requirement_type: str
    snapshot_id: uuid.UUID
    state_id: uuid.UUID
    screen_title: str
    screen_route: str | None
    screen_kind: str
    confidence: float
    rationale: str
    citations: list[Any]
    retrieval_score: float
    method: str
    review_status: str
    effective: bool  # counts toward coverage at the current threshold
    updated_at: datetime


class MappingUpdate(BaseModel):
    review_status: Literal["proposed", "confirmed", "rejected"]


class MappingCreate(BaseModel):
    requirement_id: uuid.UUID
    state_id: uuid.UUID
    note: str = Field(default="", max_length=600)


class GapPublic(BaseModel):
    id: uuid.UUID
    snapshot_id: uuid.UUID
    direction: str
    code: str
    reason: str
    evidence: dict[str, Any]
    severity: str
    status: str
    requirement_id: uuid.UUID | None
    req_key: str | None
    requirement_title: str | None
    requirement_type: str | None
    requirement_status: str | None
    state_id: uuid.UUID | None
    screen_title: str | None
    screen_route: str | None


class GapUpdate(BaseModel):
    status: Literal["open", "accepted", "resolved"]


class CoverageSummary(BaseModel):
    snapshot_id: uuid.UUID | None
    threshold: float
    requirements_total: int
    requirements_matched: int
    requirement_gaps: int
    screens_total: int
    feature_gaps: int
    mappings: int
    confirmed: int
    rejected: int
    last_run_id: uuid.UUID | None
    # The crawl stopped at a budget: a requirement gap may only mean the screen was not reached.
    crawl_partial: bool = False


# ---------------------------------------------------------------------------- test cases
class GenerateRequest(BaseModel):
    requirement_ids: list[uuid.UUID] | None = (
        None  # default: every requirement that can be generated
    )


class TestCasePublic(BaseModel):
    id: uuid.UUID
    tc_key: str
    requirement_id: uuid.UUID
    req_key: str
    requirement_title: str
    also_covers: list[str]
    state_id: uuid.UUID | None
    snapshot_id: uuid.UUID
    module: str | None
    title: str
    preconditions: list[Any]
    steps: list[Any]
    rendered_steps: list[Any]
    test_data: list[Any]
    expected_result: str
    priority: str
    test_type: str
    role_name: str | None
    origin: str
    status: str
    evidence: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime


class TestCasesPage(BaseModel):
    data: list[TestCasePublic]
    count: int


class TestCaseStatus(BaseModel):
    status: Literal["draft", "approved", "rejected"]


class TestsSummary(BaseModel):
    total: int
    by_status: dict[str, int]
    by_type: dict[str, int]
    by_origin: dict[str, int]
    requirements_total: int
    requirements_covered: int
    last_run_id: uuid.UUID | None


# ---------------------------------------------------------------------------- review, RTM, exports
class TestCaseEdit(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    preconditions: list[str] | None = None
    rendered_steps: list[str] | None = None
    test_data: list[dict[str, str]] | None = None
    expected_result: str | None = Field(default=None, min_length=1)
    priority: Literal["P1", "P2", "P3", "P4"] | None = None
    test_type: (
        Literal[
            "positive",
            "negative",
            "boundary",
            "field_validation",
            "role_access",
            "e2e",
            "business_rule",
        ]
        | None
    ) = None
    role_name: str | None = None
    note: str = Field(default="", max_length=500)


class TestBulk(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    action: Literal["approve", "reject", "draft"]


class RegenerateRequest(BaseModel):
    feedback: str = Field(default="", max_length=2000)


class TestVersionPublic(BaseModel):
    version: int
    change_type: str
    change_note: str
    content: dict[str, Any]
    created_at: datetime


class RestoreRequest(BaseModel):
    version: int = Field(ge=1)


class RtmRow(BaseModel):
    requirement_id: uuid.UUID
    req_key: str
    title: str
    type: str
    review_status: str
    needs_decision: bool
    mapped_screens: int
    tests_own: int
    tests_linked: int
    tests_total: int
    by_type: dict[str, int]
    approved: int
    draft: int
    needs_regen: int
    rejected_tests: int
    coverage: str
    reason: str


class RtmPublic(BaseModel):
    rows: list[RtmRow]
    requirements: int
    covered: int
    uncovered: int
    needs_decision: int
    test_cases: int  # non-rejected cases; equals the sum of tests_own


class TemplateColumnPublic(BaseModel):
    key: str
    header: str


class TemplatePublic(BaseModel):
    columns: list[TemplateColumnPublic]
    id_prefix: str
    available: list[TemplateColumnPublic]


class TemplateUpdate(BaseModel):
    columns: list[TemplateColumnPublic] = Field(min_length=1)
    id_prefix: str = Field(default="TC", pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,9}$")


class RunMetric(BaseModel):
    run_id: uuid.UUID
    kind: str
    status: str
    seconds: float | None
    input_tokens: int
    output_tokens: int
    created_at: datetime
    cost_usd: float = 0.0


class KindMetric(BaseModel):
    kind: str
    runs: int
    seconds: float
    input_tokens: int
    output_tokens: int
    cost_usd: float = 0.0


class MetricsPublic(BaseModel):
    by_kind: list[KindMetric]
    runs: list[RunMetric]
    input_tokens: int
    output_tokens: int
    # An estimate: tokens x the configured list prices (embeddings are not included)
    price_input_per_m: float = 0.0
    price_output_per_m: float = 0.0
    cost_usd: float = 0.0
