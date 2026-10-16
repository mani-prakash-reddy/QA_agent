"""The supervisor: understands the application's structure, splits the work, and sizes the team.

1. **Scouts** (one per role, cheap, through the same tool gate) log in, read the navigation and open each
   section once. They measure how big every page is (controls, forms, tabs, rows).
2. The **supervisor** (one structured-output model call) is shown what every role has: which pages all
   roles share, which only some do, and how big each is. It groups them into work packages and says which
   role should take each one. A page every role has is explored once, by one role; a page only one role
   has is explored by that role.
3. Code validates the plan (routes must have been seen by that role, no page twice, nothing left out) and
   sizes the team from the *measured* complexity: a small package gets one explorer agent, a large one up
   to three, with a step budget that grows with its size.
4. **Explorer agents** (LLM tool agents) walk their pages through the gated browser tools. A coverage
   report computed from what was recorded drives a replan for what was missed.

The supervisor and the agents decide *what to look at and who looks*; everything they do goes through the
tool gate (safe mode, the network guard, budgets, events) and what is recorded comes from the browser."""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field
from sqlmodel import Session as DbSession
from sqlmodel import select

from app.core.db import engine
from app.qa.agents.budget import BudgetExceeded, BudgetTracker
from app.qa.agents.runtime.runner import EventCallback, run_agent
from app.qa.agents.spec import AgentSpec, Budget, BudgetCaps, Scope, validate_spec
from app.qa.browser.auth import AuthError
from app.qa.browser.extract import clean_label, route_path, route_template
from app.qa.browser.session import RoleBrowser
from app.qa.config import CrawlConfig
from app.qa.crawler.session import CrawlSession
from app.qa.gate.gate import AgentContext, ToolGate
from app.qa.knowledge.recorder import KnowledgeRecorder
from app.qa.models import RoleStateAccess, UiState
from app.qa.runs.control import StopRequested

logger = logging.getLogger(__name__)

PLAN_VERSION = "supervisor-plan-v1"

PLAN_SYSTEM = """You are the supervisor of a team of QA exploration agents. Scouts have already looked at a
web application as each user role and measured every page they could reach. You decide how to divide the
exploration so that each part of the application is explored exactly once, by the role that should do it.

Rules:
- A page marked [shared: A, B] exists for several roles: assign it to ONE of them. A page marked
  [only: X] must go to role X, because only X can see it.
- Group related small pages into one package (a module); give a big or complex page its own package.
- `routes` must be copied from the page lists below, for the package's role. Never invent a route.
- `complexity` is your estimate (low / medium / high) from the measured numbers: controls, forms, tabs,
  table rows. The team size is derived from the measurements, so be honest.
- Cover every page. Skip nothing, except sign-out and pure shell controls.
- `goal` is one sentence about what to find there (forms and their validation, tabs, dialogs, what only
  this role sees), informed by the requirements when they help.
- At most {limit} packages."""

REPLAN_SYSTEM = (
    PLAN_SYSTEM
    + """

You are replanning after a coverage report. Return only packages for the pages the report lists as
missed; return none when nothing worthwhile is missing."""
)

EXPLORER_PROMPT = """You are one explorer in a team. Your part of the application: {name}.
Goal: {goal}
Role you are signed in as: {role}.
Pages assigned to you (already opened for the first one): {routes}

How to work:
- browser_snapshot shows the screen; elements have handles (e1, e2...). Use handles from the LATEST snapshot only.
- For each assigned page: open it with browser_navigate if you are not on it, then open each distinct tab,
  menu and dialog on it ONCE (click it, look, close it or go back).
- On a screen with a form, call probe_validation once to learn its rules. Never submit a form.
- Do not wander to pages that belong to other agents. Destructive and external actions are blocked: do not retry them.
- Stop when every assigned page has been covered, then answer in two sentences: what you found."""


# --- inventory ----------------------------------------------------------------------------------


@dataclass
class PageProbe:
    label: str
    route: str  # path (+ query, + #/hash route), no host
    elements: int = 0
    forms: int = 0
    tabs: int = 0
    rows: int = 0

    @property
    def score(self) -> int:
        """Measured size of the page: what an explorer has to look at."""
        return self.elements + 6 * self.forms + 4 * self.tabs + min(self.rows, 20)


@dataclass
class Inventory:
    role: str
    pages: dict[str, PageProbe] = field(default_factory=dict)  # key: route_template
    error: str | None = None


def role_pages(snapshot_id: uuid.UUID, role_id: uuid.UUID) -> set[str]:
    with DbSession(engine) as s:
        rows = s.exec(
            select(UiState.sample_url)
            .join(RoleStateAccess, RoleStateAccess.state_id == UiState.id)  # type: ignore[arg-type]
            .where(
                UiState.snapshot_id == snapshot_id,
                UiState.kind == "page",
                RoleStateAccess.role_id == role_id,
                RoleStateAccess.access == "visible",
            )
        ).all()
    return {route_path(u) for u in rows if u}


def route_of(url: str) -> str:
    p = urlsplit(url)
    return (
        (p.path or "/")
        + (f"?{p.query}" if p.query else "")
        + (f"#{p.fragment}" if p.fragment else "")
    )


def pretty_label(label: str, route: str) -> str:
    return label.strip() or route


@dataclass
class RoleSession:
    """One logged-in browser of one role, with the recorder that writes what it sees."""

    role: str
    role_id: uuid.UUID
    rb: RoleBrowser
    crawl: CrawlSession
    rec: KnowledgeRecorder


class SessionPool:
    """Logged-in browsers per role. A role is logged in again for each parallel agent (up to its limit);
    a role whose login needs a person (a typed code) is limited to one session."""

    def __init__(
        self,
        opener: Callable[[str], Awaitable[RoleSession]],
        limits: dict[str, int],
    ) -> None:
        self._open, self._limits = opener, limits
        self._idle: dict[str, list[RoleSession]] = {}
        self._made: dict[str, int] = {}
        self._first: dict[str, RoleSession] = {}
        self.created: list[RoleSession] = []
        self._lock = asyncio.Condition()

    async def acquire(self, role: str) -> RoleSession:
        async with self._lock:
            while True:
                if self._idle.get(role):
                    return self._idle[role].pop()
                if self._made.get(role, 0) < self._limits.get(role, 1):
                    self._made[role] = self._made.get(role, 0) + 1
                    break
                await self._lock.wait()
        try:
            session = await self._open(role)
        except BaseException:
            async with self._lock:
                self._made[role] -= 1
                self._lock.notify_all()
            raise
        self._first.setdefault(role, session)
        self.created.append(session)
        return session

    async def release(self, session: RoleSession) -> None:
        async with self._lock:
            self._idle.setdefault(session.role, []).append(session)
            self._lock.notify_all()

    def primary(self, role: str) -> RoleSession | None:
        return self._first.get(role)

    def all(self) -> list[RoleSession]:
        return [s for xs in self._idle.values() for s in xs]

    async def close(self) -> None:
        seen: set[int] = set()
        for s in [*self._first.values(), *self.all()]:
            if id(s) in seen:
                continue
            seen.add(id(s))
            await s.rb.close()


# --- plan ---------------------------------------------------------------------------------------


class PkgPlan(BaseModel):
    name: str = Field(max_length=100)
    role: str = Field(max_length=100)
    routes: list[str] = Field(default_factory=list)
    goal: str = Field(default="", max_length=300)
    complexity: Literal["low", "medium", "high"] = "medium"


class SupPlan(BaseModel):
    rationale: str = Field(default="", max_length=600)
    packages: list[PkgPlan] = Field(default_factory=list)


@dataclass
class Task:
    """One explorer agent's assignment."""

    name: str
    role: str
    routes: list[str]
    goal: str
    complexity: str
    score: int
    steps: int
    part: str = ""  # "2/3" when a big package is split across agents


def team_size(score: int, routes: int, cap: int) -> int:
    n = 1 if score < 60 else 2 if score < 160 else 3
    return max(1, min(n, cap, routes))


def complexity_of(score: int) -> str:
    return "low" if score < 60 else "medium" if score < 160 else "high"


def build_tasks(
    plan: SupPlan,
    inventories: dict[str, Inventory],
    cfg: CrawlConfig,
    *,
    seen_routes: set[str] | None = None,
) -> list[Task]:
    """Validate the plan against what the scouts saw and size the team from measured complexity."""
    seen = seen_routes if seen_routes is not None else set()
    by_key: dict[str, dict[str, PageProbe]] = {
        role: inv.pages for role, inv in inventories.items()
    }
    holders: dict[str, list[str]] = {}
    for role, role_pages_ in by_key.items():
        for key in role_pages_:
            holders.setdefault(key, []).append(role)

    claimed: dict[
        str, dict[str, PageProbe]
    ] = {}  # role -> key -> page (shared pages go to one role)
    packages: list[
        tuple[str, str, list[str], str, str]
    ] = []  # name, role, keys, goal, complexity

    def route_key(role: str, route: str) -> str | None:
        want = route_template(urljoin("http://x", route))
        return want if want in by_key.get(role, {}) else None

    for pkg in plan.packages[: cfg.max_modules]:
        if pkg.role not in by_key:
            continue
        keys: list[str] = []
        for r in pkg.routes:
            k = route_key(pkg.role, r)
            if k and k not in seen and k not in keys:
                keys.append(k)
        if not keys:
            continue
        seen.update(keys)
        packages.append((pkg.name, pkg.role, keys, pkg.goal, pkg.complexity))

    # Anything the plan left out is still explored: by its only holder, or by the first role that has it.
    left: dict[str, list[str]] = {}
    for key, roles in holders.items():
        if key not in seen:
            left.setdefault(roles[0], []).append(key)
            seen.add(key)
    for role, keys in left.items():
        packages.append(
            (f"Other pages ({role})", role, keys, "Explore these pages.", "low")
        )

    tasks: list[Task] = []
    for name, role, keys, goal, _claimed_complexity in packages:
        group = [by_key[role][k] for k in keys]
        score = sum(p.score for p in group)
        n = team_size(score, len(group), cfg.max_agents_per_package)
        chunks: list[list[PageProbe]] = [[] for _ in range(n)]
        loads = [0] * n
        for p in sorted(
            group, key=lambda x: -x.score
        ):  # biggest first onto the lightest agent
            i = loads.index(min(loads))
            chunks[i].append(p)
            loads[i] += p.score
        for i, chunk in enumerate(chunks):
            if not chunk:
                continue
            sc = sum(p.score for p in chunk)
            tasks.append(
                Task(
                    name=name,
                    role=role,
                    routes=[p.route for p in chunk],
                    goal=goal or f"Explore {name}",
                    complexity=complexity_of(score),
                    score=sc,
                    steps=max(25, min(cfg.agent_steps_per_module, 20 + sc // 5)),
                    part=f"{i + 1}/{n}" if n > 1 else "",
                )
            )
        claimed.setdefault(role, {}).update({k: by_key[role][k] for k in keys})
    return tasks


def inventory_text(inventories: dict[str, Inventory]) -> str:
    holders: dict[str, list[str]] = {}
    for role, inv in inventories.items():
        for key in inv.pages:
            holders.setdefault(key, []).append(role)
    out: list[str] = []
    for role, inv in inventories.items():
        out.append(
            f"Role {role}: {len(inv.pages)} pages"
            + (f" (scout problem: {inv.error})" if inv.error else "")
        )
        for key, p in inv.pages.items():
            who = holders[key]
            mark = (
                f"[shared: {', '.join(who)}]" if len(who) > 1 else f"[only: {who[0]}]"
            )
            out.append(
                f"  {p.route} — {pretty_label(p.label, p.route)} {mark} "
                f"controls={p.elements} forms={p.forms} tabs={p.tabs} rows={p.rows}"
            )
    return "\n".join(out)


def fallback_plan(inventories: dict[str, Inventory]) -> SupPlan:
    """No usable plan from the model: one package per navigation section, role-correct by construction."""
    pkgs: list[PkgPlan] = []
    taken: set[str] = set()
    for role, inv in inventories.items():
        for key, p in inv.pages.items():
            if key in taken:
                continue
            taken.add(key)
            pkgs.append(
                PkgPlan(
                    name=pretty_label(p.label, p.route)[:100],
                    role=role,
                    routes=[p.route],
                    goal=f"Explore {pretty_label(p.label, p.route)}",
                )
            )
    return SupPlan(rationale="fallback: one package per page", packages=pkgs)


# --- the supervisor --------------------------------------------------------------------------


class Supervisor:
    def __init__(
        self,
        *,
        ctx: AgentContext,
        gate: ToolGate,
        cfg: CrawlConfig,
        caps: BudgetCaps,
        pool: SessionPool,
        roles: dict[str, uuid.UUID],
        snapshot_id: uuid.UUID,
        requirements: list[str],
        planner: BaseChatModel,
        explorer: BaseChatModel,
    ) -> None:
        self.ctx, self.gate, self.cfg, self.caps = ctx, gate, cfg, caps
        self.pool, self.roles, self.snapshot_id = pool, roles, snapshot_id
        self.requirements, self.planner, self.explorer = requirements, planner, explorer
        self.events = ctx.events
        self.spawned = 0
        self.results: list[dict[str, Any]] = []
        self.role_state: dict[str, str] = dict.fromkeys(roles, "succeeded")
        self.role_detail: dict[str, str] = {}

    # -- agents ---------------------------------------------------------------------------
    def _child_ctx(
        self,
        kind: Literal["scout", "explorer"],
        goal: str,
        role: str,
        steps: int,
        session: RoleSession,
    ) -> AgentContext:
        spec = validate_spec(
            AgentSpec(
                kind=kind,
                goal=goal,
                scope=Scope(role_ref=role, max_depth=self.cfg.max_depth),
                budget=Budget(
                    max_steps=steps,
                    max_tokens=min(150_000, self.caps.max_tokens_per_agent),
                    max_seconds=min(600, self.caps.max_seconds_per_agent),
                ),
            ),
            self.caps,
            known_roles=set(self.roles),
        )
        return AgentContext(
            run_id=self.ctx.run_id,
            agent_id=str(uuid.uuid4()),
            parent_id=self.ctx.agent_id,
            spec=spec,
            budget=BudgetTracker(spec.budget),
            events=self.events,
            control=self.ctx.control,
            scanner=self.ctx.scanner,
            resources={"crawl": session.crawl},
        )

    async def _announce(self, child: AgentContext, role: str) -> None:
        await self.events.emit(
            "agent_spawned",
            agent_id=child.agent_id,
            parent_id=self.ctx.agent_id,
            spec=child.spec.model_dump(),
            role=role,
        )
        await self.events.emit(
            "agent_status", agent_id=child.agent_id, status="running", role=role
        )

    # -- 1. scouts ------------------------------------------------------------------------
    async def scout(self, role: str) -> Inventory:
        inv = Inventory(role)
        try:
            session = await self.pool.acquire(role)
        except AuthError as exc:
            inv.error = f"login failed: {exc}"
            self.role_state[role], self.role_detail[role] = "failed", inv.error
            await self.events.emit(
                "error", agent_id=self.ctx.agent_id, message=f"{role}: {inv.error}"
            )
            return inv
        child = self._child_ctx(
            "scout",
            f"Scout the application as the '{role}' role: read the navigation, open each section once",
            role,
            self.cfg.scout_max_pages * 2 + 5,
            session,
        )
        await self._announce(child, role)
        session.rec.agent_id = child.agent_id
        status, detail = "succeeded", ""
        try:
            landing = await self.gate.invoke(child, "browser_snapshot", {})
            landing_url = session.rb.current.url if session.rb.current else ""
            extra = await self._menu_links(child, session, landing_url)
            targets = self._nav_targets(session, landing, extra)
            for label, url in targets:
                res = await self.gate.invoke(child, "browser_navigate", {"route": url})
                snap = session.rb.current
                if res.get("error") or res.get("blocked") or snap is None:
                    continue
                chrome = {"nav", "header", "footer"}
                inv.pages[route_template(snap.url)] = PageProbe(
                    label=label or snap.heading or snap.title,
                    route=route_of(snap.url),
                    elements=sum(
                        1
                        for e in snap.elements
                        if e.region not in chrome and not e.in_row
                    ),
                    forms=len(snap.forms),
                    tabs=sum(1 for e in snap.elements if e.kind == "tab"),
                    rows=snap.row_count,
                )
            if not inv.pages and (snap := session.rb.current):
                inv.pages[route_template(snap.url)] = PageProbe(
                    snap.heading or snap.title,
                    route_of(snap.url),
                    elements=len(snap.elements),
                    forms=len(snap.forms),
                )
        except StopRequested:
            status = "stopped"
            raise
        except BudgetExceeded as exc:
            status, detail = "budget_exceeded", exc.which
        finally:
            await self.events.emit(
                "agent_finished",
                agent_id=child.agent_id,
                status=status,
                detail=detail,
                result=f"{len(inv.pages)} pages measured",
                budget=child.budget.snapshot(),
            )
            await self.pool.release(session)
        return inv

    async def _menu_links(
        self, child: AgentContext, session: RoleSession, landing_url: str
    ) -> list[tuple[str, str]]:
        """Links that live inside menus opened from the app shell (an account menu with Settings)."""
        snap = session.rb.current
        if snap is None:
            return []
        triggers = [
            e
            for e in snap.elements
            if e.kind == "button"
            and e.region in {"nav", "header"}
            and clean_label(e.label)[0]
            and not e.disabled
        ][:6]
        found: list[tuple[str, str]] = []
        for trig in triggers:
            cur = session.rb.current
            el = cur.by_key(trig.key) if cur else None
            if el is None:
                await self.gate.invoke(child, "browser_snapshot", {})
                cur = session.rb.current
                el = cur.by_key(trig.key) if cur else None
            if el is None:
                continue
            res = await self.gate.invoke(child, "browser_click", {"handle": el.h})
            now = session.rb.current
            if res.get("effect") == "overlay_opened" and now is not None:
                found += [
                    (clean_label(e.label)[0], urljoin(now.url, e.href))
                    for e in now.elements
                    if e.kind in {"link", "menuitem"}
                    and e.href
                    and e.region == "overlay"
                    and not e.href.startswith(("mailto:", "tel:", "javascript:"))
                ]
            if landing_url:  # close the menu: a fresh load of where we were
                await self.gate.invoke(
                    child, "browser_navigate", {"route": landing_url, "fresh": True}
                )
        return found

    def _nav_targets(
        self,
        session: RoleSession,
        landing: dict[str, Any],
        extra: list[tuple[str, str]] | None = None,
    ) -> list[tuple[str, str]]:
        crawl = session.crawl
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        snap = session.rb.current
        if snap is None:
            return out
        links = [
            (clean_label(e.label)[0], urljoin(snap.url, e.href))
            for e in snap.elements
            if e.kind == "link"
            and e.href
            and e.region in {"nav", "header"}
            and not e.href.startswith(("mailto:", "tel:", "javascript:"))
        ]
        # the landing page itself is a page too
        links.insert(0, (snap.heading or snap.title, snap.url))
        links += extra or []
        for label, url in links:
            if not crawl.is_internal(url) or not crawl.path_allowed(url)[0]:
                continue
            if crawl.policy.classify(label, url).blocked:
                continue
            key = route_template(url)
            if key in seen:
                continue
            seen.add(key)
            out.append((label, url))
            if len(out) >= self.cfg.scout_max_pages:
                break
        return out

    # -- 2/3. plan --------------------------------------------------------------------------
    async def plan(
        self, inventories: dict[str, Inventory], *, system: str, extra: str = ""
    ) -> SupPlan:
        reqs = "\n".join(f"- {r}" for r in self.requirements[:40]) or "(none supplied)"
        user = f"{inventory_text(inventories)}\n\nRequirements:\n{reqs}{extra}"
        structured = self.planner.with_structured_output(SupPlan)
        try:
            raw = await structured.ainvoke(
                [("system", system.format(limit=self.cfg.max_modules)), ("user", user)],
                config={"callbacks": [EventCallback(self.ctx)]},
            )
            return raw if isinstance(raw, SupPlan) else SupPlan.model_validate(raw)
        except Exception as exc:  # a failed plan is reported and replaced, never fatal
            await self.events.emit(
                "error",
                agent_id=self.ctx.agent_id,
                message=f"supervisor plan failed ({type(exc).__name__}); using one package per page",
            )
            return fallback_plan(inventories)

    # -- 4. explorers -------------------------------------------------------------------------
    async def run_task(self, task: Task, sem: asyncio.Semaphore) -> None:
        async with sem:
            if self.spawned >= self.caps.max_total_spawns:
                return
            self.spawned += 1
            await self.ctx.control.checkpoint(self.ctx.agent_id)
            try:
                session = await self.pool.acquire(task.role)
            except AuthError as exc:
                self.role_state[task.role] = "failed"
                self.role_detail[task.role] = f"login failed: {exc}"
                return
            title = f"{task.name}{f' ({task.part})' if task.part else ''}"
            child = self._child_ctx(
                "explorer",
                f"{title}: {task.goal} — as '{task.role}' ({task.complexity} complexity, {len(task.routes)} page(s))",
                task.role,
                task.steps,
                session,
            )
            session.rec.agent_id = child.agent_id
            status = "succeeded"
            try:
                res = await self.gate.invoke(
                    child, "browser_navigate", {"route": task.routes[0]}
                )
                if res.get("error") or res.get("blocked"):
                    await session.rec.log(
                        "error",
                        str(res.get("error") or res.get("reason")),
                        url=task.routes[0],
                        action="navigate",
                    )
                result = await run_agent(
                    child,
                    self.explorer,
                    self.gate,
                    EXPLORER_PROMPT.format(
                        name=title,
                        goal=task.goal,
                        role=task.role,
                        routes=", ".join(task.routes),
                    ),
                )
                status = result.status
                if status == "stopped":
                    raise StopRequested
            except BudgetExceeded:
                status = "budget_exceeded"
            finally:
                self.results.append(
                    {
                        "package": title,
                        "role": task.role,
                        "status": status,
                        "complexity": task.complexity,
                        "routes": len(task.routes),
                    }
                )
                if status == "budget_exceeded":
                    self.role_state[task.role] = "budget_exceeded"
                await self.pool.release(session)

    def coverage_gaps(self, inventories: dict[str, Inventory]) -> dict[str, list[str]]:
        """Per role: pages the scout saw that have no recorded screen for that role."""
        out: dict[str, list[str]] = {}
        for role, inv in inventories.items():
            seen = role_pages(self.snapshot_id, self.roles[role])
            missed = [
                p.route
                for p in inv.pages.values()
                if route_path(urljoin("http://x", p.route)) not in seen
            ]
            if missed:
                out[role] = missed
        return out

    async def run(self, roles: list[str]) -> dict[str, Any]:
        sem = asyncio.Semaphore(self.cfg.max_concurrent_agents)
        # 1. understand the application: scouts, one per role, in parallel
        invs = await asyncio.gather(*[self.scout(r) for r in roles])
        inventories = {i.role: i for i in invs if i.pages}
        if not inventories:
            return self.summary([], 0)
        shared = (
            {
                k
                for k in next(iter(inventories.values())).pages
                if all(k in i.pages for i in inventories.values())
            }
            if len(inventories) > 1
            else set()
        )
        await self.events.emit(
            "agent_status",
            agent_id=self.ctx.agent_id,
            status="structure_understood",
            pages={r: len(i.pages) for r, i in inventories.items()},
            shared=len(shared),
        )
        # 2. divide the work
        plan = await self.plan(inventories, system=PLAN_SYSTEM)
        if plan.rationale:
            await self.events.emit(
                "llm_message", agent_id=self.ctx.agent_id, text=plan.rationale
            )
        seen: set[str] = set()
        tasks = build_tasks(plan, inventories, self.cfg, seen_routes=seen)
        await self._announce_plan(tasks)
        replans = 0
        for round_no in range(1 + max(0, self.cfg.agentic_replans)):
            # 3. spawn the team, sized by complexity
            await asyncio.gather(*[self.run_task(t, sem) for t in tasks])
            gaps = await asyncio.to_thread(self.coverage_gaps, inventories)
            await self.events.emit(
                "agent_status",
                agent_id=self.ctx.agent_id,
                status="coverage_report",
                round=round_no + 1,
                missed={r: len(v) for r, v in gaps.items()},
            )
            if not gaps or round_no >= self.cfg.agentic_replans:
                break
            replans += 1
            report = (
                "\n\nCoverage report (pages with no recorded screen):\n"
                + "\n".join(f"- {r}: {', '.join(v[:12])}" for r, v in gaps.items())
            )
            narrowed = {
                r: Inventory(
                    r,
                    {
                        k: p
                        for k, p in inv.pages.items()
                        if p.route in set(gaps.get(r, []))
                    },
                )
                for r, inv in inventories.items()
            }
            plan2 = await self.plan(narrowed, system=REPLAN_SYSTEM, extra=report)
            tasks = build_tasks(plan2, narrowed, self.cfg, seen_routes=set())
            if not tasks:
                break
            await self._announce_plan(tasks)
        return self.summary(tasks, replans)

    async def _announce_plan(self, tasks: list[Task]) -> None:
        await self.events.emit(
            "agent_status",
            agent_id=self.ctx.agent_id,
            status="plan_ready",
            agents=len(tasks),
            packages=[
                {
                    "name": t.name,
                    "role": t.role,
                    "complexity": t.complexity,
                    "part": t.part,
                    "pages": len(t.routes),
                }
                for t in tasks
            ],
        )

    def summary(self, tasks: list[Task], replans: int) -> dict[str, Any]:
        return {
            "packages": self.results,
            "replans": replans,
            "roles": dict(self.role_state),
            "role_detail": dict(self.role_detail),
        }
