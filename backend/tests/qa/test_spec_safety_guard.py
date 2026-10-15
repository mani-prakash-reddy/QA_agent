from dataclasses import dataclass

import pytest

from app.qa.agents.budget import BudgetExceeded, BudgetTracker
from app.qa.agents.spec import (
    AgentSpec,
    Budget,
    BudgetCaps,
    Scope,
    SpecError,
    validate_spec,
)
from app.qa.gate.guard import RequestGuard, is_graphql_mutation
from app.qa.gate.safety import SafetyPolicy

CAPS = BudgetCaps()
ROLES = {"admin", "user"}


def explorer(**kw) -> AgentSpec:  # type: ignore[no-untyped-def]
    return AgentSpec(
        kind="explorer", goal="explore users", scope=Scope(role_ref="admin"), **kw
    )


def test_spec_defaults_to_kind_tools() -> None:
    spec = validate_spec(explorer(), CAPS, known_roles=ROLES)
    assert "browser_click" in spec.tools and "spawn_agent" not in spec.tools


def test_spec_cannot_widen_tools() -> None:
    with pytest.raises(SpecError):
        validate_spec(
            explorer(tools=["browser_click", "spawn_agent"]), CAPS, known_roles=ROLES
        )


def test_spec_respects_project_allowlist() -> None:
    spec = validate_spec(
        explorer(), CAPS, known_roles=ROLES, project_tool_allowlist={"browser_snapshot"}
    )
    assert spec.tools == ["browser_snapshot"]


def test_spec_rejects_unknown_role_missing_role_and_big_budget() -> None:
    with pytest.raises(SpecError):
        validate_spec(
            AgentSpec(kind="explorer", goal="g", scope=Scope(role_ref="ghost")),
            CAPS,
            known_roles=ROLES,
        )
    with pytest.raises(SpecError):
        validate_spec(AgentSpec(kind="explorer", goal="g"), CAPS, known_roles=ROLES)
    with pytest.raises(SpecError):
        validate_spec(explorer(budget=Budget(max_steps=10**6)), CAPS, known_roles=ROLES)


def test_budget_tracker_caps() -> None:
    t = BudgetTracker(Budget(max_steps=2, max_tokens=100))
    t.step()
    t.step()
    with pytest.raises(BudgetExceeded) as e:
        t.step()
    assert e.value.which == "steps"
    t2 = BudgetTracker(Budget(max_tokens=10))
    t2.add_tokens(8, 5)
    with pytest.raises(BudgetExceeded):
        t2.check()


@pytest.mark.parametrize(
    "label",
    [
        "Delete",
        "Delete user",
        "Log Out",
        "Sign out",
        "Pay now",
        "Remove item",
        "Place order",
    ],
)
def test_destructive_labels_blocked(label: str) -> None:
    assert SafetyPolicy().classify(label).blocked


@pytest.mark.parametrize(
    "label",
    [
        "Add User",
        "Save",
        "Items",
        "Edit",
        "Cancel",
        "Undelete history",
        "Show Orders and Payment Menu",
        "Payment methods",
        "Payments overview",
    ],
)
def test_normal_labels_allowed(label: str) -> None:
    assert not SafetyPolicy().classify(label).blocked


def test_safety_policy_allowlist_and_override() -> None:
    assert (
        not SafetyPolicy(allowlist=[r"^delete test item$"])
        .classify("Delete test item")
        .blocked
    )
    assert not SafetyPolicy(allow_destructive=True).classify("Delete").blocked
    assert SafetyPolicy().classify("Go", href="/auth/logout").blocked


@dataclass
class FakeRequest:
    method: str
    url: str
    post_data: str | None = None


class FakeRoute:
    def __init__(self, request: FakeRequest) -> None:
        self.request, self.outcome = request, None

    async def abort(self, error_code: str | None = None) -> None:
        self.outcome = "aborted"

    async def continue_(self) -> None:
        self.outcome = "continued"


async def run_guard(guard: RequestGuard, req: FakeRequest) -> str | None:
    route = FakeRoute(req)
    await guard.handle(route)
    return route.outcome


@pytest.mark.anyio
async def test_request_guard_blocks_mutations_only_when_armed() -> None:
    blocked: list[tuple[str, str, str]] = []

    async def on_block(m: str, u: str, r: str) -> None:
        blocked.append((m, u, r))

    guard = RequestGuard(on_block, safe_post_patterns=[r"/api/search"])
    post = FakeRequest("POST", "http://app/api/items")
    assert await run_guard(guard, post) == "continued"  # not armed (login phase)
    guard.arm()
    assert await run_guard(guard, post) == "aborted"
    assert (
        await run_guard(guard, FakeRequest("DELETE", "http://app/api/items/1"))
        == "aborted"
    )
    assert (
        await run_guard(guard, FakeRequest("GET", "http://app/api/items"))
        == "continued"
    )
    assert (
        await run_guard(guard, FakeRequest("POST", "http://app/api/search"))
        == "continued"
    )
    assert len(blocked) == 2


@pytest.mark.anyio
async def test_request_guard_blocks_graphql_mutation_even_on_safe_pattern() -> None:
    async def on_block(m: str, u: str, r: str) -> None: ...

    guard = RequestGuard(on_block, safe_post_patterns=[r"/graphql"])
    guard.arm()
    query = FakeRequest(
        "POST", "http://app/graphql", '{"query": "query { items { id } }"}'
    )
    mutation = FakeRequest(
        "POST",
        "http://app/graphql",
        '{"query": "mutation { deleteItem(id: 1) { id } }"}',
    )
    assert await run_guard(guard, query) == "continued"
    assert await run_guard(guard, mutation) == "aborted"
    assert is_graphql_mutation('[{"query": "mutation M { x }"}]')
    assert not is_graphql_mutation("not json")


@pytest.mark.parametrize(
    "label",
    ["Pay", "Pay now", "Make a payment", "Submit payment", "Confirm the payment"],
)
def test_payment_actions_are_blocked_but_payment_menus_are_not(label: str) -> None:
    assert SafetyPolicy().classify(label).blocked


@pytest.mark.anyio
async def test_realtime_transport_and_external_hosts() -> None:
    blocked: list[tuple[str, str, str]] = []

    async def on_block(m: str, u: str, r: str) -> None:
        blocked.append((m, u, r))

    guard = RequestGuard(on_block, app_origin="app.test")
    guard.arm()
    poll = FakeRequest("POST", "http://app.test/socket.io/?EIO=4&transport=polling")
    assert await run_guard(guard, poll) == "continued"  # the app's own live channel
    analytics = FakeRequest("POST", "https://collector.thirdparty.com/collect")
    assert await run_guard(guard, analytics) == "aborted"
    assert blocked == [
        (
            "POST",
            "https://collector.thirdparty.com/collect",
            "external non-read request",
        )
    ]
    assert (
        await run_guard(guard, FakeRequest("POST", "http://app.test/api/items"))
        == "aborted"
    )
    assert (
        await run_guard(guard, FakeRequest("GET", "https://cdn.thirdparty.com/lib.js"))
        == "continued"
    )


@pytest.mark.anyio
async def test_explicit_allow_pattern_beats_external_rule() -> None:
    async def on_block(m: str, u: str, r: str) -> None: ...

    guard = RequestGuard(
        on_block, app_origin="app.test", safe_post_patterns=[r"api\.example\.com/query"]
    )
    guard.arm()
    assert (
        await run_guard(guard, FakeRequest("POST", "https://api.example.com/query"))
        == "continued"
    )
    assert (
        await run_guard(guard, FakeRequest("POST", "https://api.example.com/other"))
        == "aborted"
    )
