"""Supervised crawl with scripted models: scouts measure each role, the supervisor divides the work, the team
is sized by measured complexity, explorers walk their pages. The browser, gate, guard and recorder are real;
only the models are scripted."""

import asyncio
import json
from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from cryptography.fernet import Fernet
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.models import User
from app.qa.config import CrawlConfig
from app.qa.crawler.supervisor import (
    Inventory,
    PageProbe,
    PkgPlan,
    SupPlan,
    build_tasks,
    fallback_plan,
)
from app.qa.llm.fake import RuleChatModel
from app.qa.models import (
    AgentEvent,
    AgentRun,
    AppRole,
    AppSnapshot,
    Project,
    RoleCredential,
    Run,
    UiState,
)
from app.qa.runs.executor import execute_run
from app.qa.security.crypto import encrypt_json
from tests.qa.conftest import _chromium_available
from tests.qa.fixture_app import USERS, FixtureServer

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


class ModuleExplorer(BaseChatModel):
    """Stateless scripted explorer: snapshot, press 'Add Item' if present, then report."""

    @property
    def _llm_type(self) -> str:
        return "module-explorer"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ModuleExplorer:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Any = None,
        run_manager: Any = None,
        **kw: Any,
    ) -> ChatResult:
        calls = [m for m in messages if isinstance(m, AIMessage) and m.tool_calls]
        tools = [m for m in messages if isinstance(m, ToolMessage)]
        usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        if not calls:
            msg = AIMessage(
                content="",
                tool_calls=[{"name": "browser_snapshot", "args": {}, "id": "s1"}],
                usage_metadata=usage,
            )
        elif len(calls) == 1:
            handle = None
            try:
                data = json.loads(str(tools[-1].content))
                handle = next(
                    (
                        e["h"]
                        for e in data.get("elements", [])
                        if e["label"] == "Add Item"
                    ),
                    None,
                )
            except Exception:
                pass
            if handle:
                msg = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "browser_click",
                            "args": {"handle": handle},
                            "id": "c1",
                        }
                    ],
                    usage_metadata=usage,
                )
            else:
                msg = AIMessage(content="Nothing more here.", usage_metadata=usage)
        else:
            msg = AIMessage(content="Explored the module.", usage_metadata=usage)
        return ChatResult(generations=[ChatGeneration(message=msg)])


def plan_handler(prompt: str) -> dict[str, Any]:
    """A supervisor that follows the rules: shared pages to one role, role-only pages to that role.
    It also (wrongly) offers a shared page to both roles and an invented route: code must cope."""
    packages: list[dict[str, Any]] = []
    role = ""
    for line in prompt.splitlines():
        if line.startswith("Role "):
            role = line.split()[1].rstrip(":")
        elif line.startswith("  /") and " — " in line:
            route = line.split()[0]
            if "[only:" in line or role == "Admin":
                packages.append(
                    {
                        "name": route,
                        "role": role,
                        "routes": [route],
                        "goal": f"Explore {route}",
                        "complexity": "low",
                    }
                )
            elif (
                "[shared:" in line
            ):  # offered to the second role as well: a duplicate to be dropped
                packages.append(
                    {
                        "name": route,
                        "role": role,
                        "routes": [route],
                        "goal": "dup",
                        "complexity": "low",
                    }
                )
    packages.append(
        {
            "name": "Invented",
            "role": "Admin",
            "routes": ["/nope"],
            "goal": "invented",
            "complexity": "low",
        }
    )
    return {
        "rationale": "shared pages once, role-only pages by their role",
        "packages": packages,
    }


@pytest.fixture(scope="module")
def agentic(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    if not _chromium_available():
        pytest.skip("chromium is not installed")
    mp = pytest.MonkeyPatch()
    mp.setattr(
        settings, "CREDENTIAL_ENCRYPTION_KEY", SecretStr(Fernet.generate_key().decode())
    )
    mp.setattr(settings, "ARTIFACT_DIR", str(tmp_path_factory.mktemp("artifacts")))
    mp.setattr(
        "app.qa.runs.kinds.crawl.make_explorer_models",
        lambda rc, cfg: (
            RuleChatModel(handler=plan_handler, tool_name="SupPlan"),
            ModuleExplorer(),
        ),
    )
    server = FixtureServer().start()
    with Session(engine) as s:
        owner = s.exec(select(User)).first()
        assert owner
        p = Project(
            owner_id=owner.id,
            name="fixture-supervised",
            target_base_url=server.url,
            environment_ack=True,
            crawl_config={
                "explorer_mode": "agentic",
                "max_depth": 2,
                "agent_steps_per_module": 10,
            },
        )
        s.add(p)
        s.flush()
        for name, email in (
            ("Admin", "admin@fixture.test"),
            ("User", "user@fixture.test"),
        ):
            r = AppRole(project_id=p.id, name=name, login_recipe={})
            s.add(r)
            s.flush()
            s.add(
                RoleCredential(
                    role_id=r.id,
                    ciphertext=encrypt_json(
                        {"username": email, "password": USERS[email][0]}
                    ),
                )
            )
        run = Run(project_id=p.id, kind="crawl", config_snapshot={})
        s.add(run)
        s.commit()
        run_id, project_id = run.id, p.id

    async def go() -> str:
        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            return await execute_run(run_id, redis)
        finally:
            await redis.aclose()

    status = asyncio.run(go())
    with Session(engine) as s:
        snap = s.exec(select(AppSnapshot).where(AppSnapshot.run_id == run_id)).one()
        agents = list(s.exec(select(AgentRun).where(AgentRun.run_id == run_id)))
        states = list(s.exec(select(UiState).where(UiState.snapshot_id == snap.id)))
        events = list(s.exec(select(AgentEvent).where(AgentEvent.run_id == run_id)))
        yield {
            "status": status,
            "snap": snap,
            "agents": agents,
            "states": states,
            "events": events,
            "server": server,
        }
    server.stop()
    with Session(engine) as s:
        pr = s.get(Project, project_id)
        if pr:
            s.delete(pr)
            s.commit()
    mp.undo()


def test_run_succeeds_and_pages_are_recorded(agentic: dict[str, Any]) -> None:
    assert agentic["status"] == "succeeded"
    titles = {(st.kind, st.title) for st in agentic["states"]}
    for page in ("Items", "Settings", "Admin"):
        assert ("page", page) in titles, titles


def test_one_scout_per_role_then_a_plan(agentic: dict[str, Any]) -> None:
    scouts = [a for a in agentic["agents"] if a.kind == "scout"]
    assert sorted(a.spec["scope"]["role_ref"] for a in scouts) == ["Admin", "User"]
    plan = [e for e in agentic["events"] if e.payload.get("status") == "plan_ready"]
    assert plan, "the supervisor announced its plan"
    assert any(
        e.payload.get("status") == "structure_understood" for e in agentic["events"]
    )


def test_team_is_sized_to_the_work_not_to_the_accounts(agentic: dict[str, Any]) -> None:
    explorers = [
        a
        for a in agentic["agents"]
        if a.kind == "explorer" and "module" not in a.spec["goal"]
    ]
    goals = [a.spec["goal"] for a in explorers]
    # the admin-only page is explored by Admin; the invented route and the duplicate offer spawn nothing
    assert any("/admin" in g and "'Admin'" in g for g in goals), goals
    assert not any("/admin" in g and "'User'" in g for g in goals)
    assert not any("/nope" in g for g in goals)
    pages = [g.split(":")[0] for g in goals]
    assert len(pages) == len(set(pages)), "every page is explored once: " + str(pages)


def test_supervised_run_never_mutates(agentic: dict[str, Any]) -> None:
    assert agentic["server"].mutations == []


# --- the planning rules, without a browser --------------------------------------------------------


def inv(role: str, *pages: tuple[str, int, int]) -> Inventory:
    return Inventory(
        role,
        {
            route: PageProbe(route.strip("/") or "home", route, elements=el, forms=fo)
            for route, el, fo in pages
        },
    )


def test_build_tasks_validates_and_sizes() -> None:
    invs = {
        "A": inv(
            "A", ("/", 10, 0), ("/small", 10, 0), ("/big", 150, 6), ("/only-a", 20, 1)
        ),
        "B": inv(
            "B", ("/", 10, 0), ("/small", 10, 0), ("/big", 150, 6), ("/only-b", 20, 1)
        ),
    }
    cfg = CrawlConfig()
    plan = SupPlan(
        packages=[
            PkgPlan(name="shared", role="A", routes=["/", "/small"]),
            PkgPlan(
                name="shared again", role="B", routes=["/small", "/only-b"]
            ),  # /small is already taken
            PkgPlan(
                name="wrong", role="B", routes=["/only-a"]
            ),  # B cannot see it: dropped, A's leftover picks it up
            PkgPlan(name="invented", role="A", routes=["/nope"]),
            PkgPlan(
                name="big", role="A", routes=["/big"], complexity="low"
            ),  # the model says low; measurement says high
        ]
    )
    tasks = build_tasks(plan, invs, cfg)
    explored = [(t.role, r) for t in tasks for r in t.routes]
    assert len(explored) == len(set(explored))
    assert {r for _, r in explored} == {"/", "/small", "/big", "/only-a", "/only-b"}, (
        "nothing left out"
    )
    owner = {r: role for role, r in explored}
    assert owner["/only-b"] == "B"
    assert owner["/only-a"] == "A"
    assert sum(1 for _, r in explored if r == "/small") == 1
    big = [t for t in tasks if "/big" in t.routes][0]
    assert big.complexity == "high" and big.steps > 25
    small = [t for t in tasks if "/small" in t.routes][0]
    assert small.complexity == "low"


def test_big_package_gets_more_agents_than_a_small_one() -> None:
    cfg = CrawlConfig()
    many = {
        f"/p{i}": PageProbe(f"p{i}", f"/p{i}", elements=70, forms=3) for i in range(6)
    }
    invs = {"A": Inventory("A", many)}
    big = build_tasks(
        SupPlan(packages=[PkgPlan(name="all", role="A", routes=list(many))]), invs, cfg
    )
    assert len(big) == cfg.max_agents_per_package
    tiny = {"/x": PageProbe("x", "/x", elements=5)}
    one = build_tasks(
        SupPlan(packages=[PkgPlan(name="x", role="A", routes=["/x"])]),
        {"A": Inventory("A", tiny)},
        cfg,
    )
    assert len(one) == 1


def test_fallback_plan_covers_everything_role_correctly() -> None:
    invs = {
        "A": inv("A", ("/", 5, 0), ("/a", 5, 0)),
        "B": inv("B", ("/", 5, 0), ("/b", 5, 0)),
    }
    tasks = build_tasks(fallback_plan(invs), invs, CrawlConfig())
    assert {(t.role, r) for t in tasks for r in t.routes} == {
        ("A", "/"),
        ("A", "/a"),
        ("B", "/b"),
    }
