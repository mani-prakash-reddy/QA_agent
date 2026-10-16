"""`crawl` run: explore the target application once per role and build a knowledge-model snapshot."""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from langchain_core.language_models.chat_models import BaseChatModel
from playwright.async_api import Browser, Page, async_playwright
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.db import engine
from app.qa.agents.budget import BudgetExceeded, BudgetTracker
from app.qa.agents.spec import AgentSpec, Budget, BudgetCaps, Scope, validate_spec
from app.qa.agents.tools.browser_tools import BROWSER_TOOLS
from app.qa.browser.auth import (
    HUMAN_CODE_TIMEOUT_S,
    AuthError,
    load_role_credentials,
)
from app.qa.browser.extract import extract
from app.qa.browser.session import RoleBrowser
from app.qa.config import CrawlConfig
from app.qa.crawler.bfs import BfsExplorer
from app.qa.crawler.session import CrawlSession
from app.qa.crawler.supervisor import RoleSession, SessionPool, Supervisor
from app.qa.gate.gate import AgentContext, ToolGate
from app.qa.gate.safety import SafetyPolicy
from app.qa.knowledge.graph import project_snapshot
from app.qa.knowledge.recorder import KnowledgeRecorder, state_summary_sync
from app.qa.llm.gateway import get_chat_model
from app.qa.models import AppRole, AppSnapshot, Requirement, Run
from app.qa.runs.context import RunContext
from app.qa.runs.control import StopRequested
from app.qa.runs.human import ask_human
from app.qa.runs.status import is_partial, status_from_agents

logger = logging.getLogger(__name__)

CRAWL_GATE = ToolGate(BROWSER_TOOLS)


_LOGIN_RECORDED: set[str] = set()


def login_page_recorder(
    rc: RunContext, rec: KnowledgeRecorder, cfg: CrawlConfig
) -> Callable[[Page], Awaitable[None]]:
    """Records the sign-in screen (once per run) as a screen of the application: requirements about
    signing in map to it, and its fields and rules feed test generation. Nothing is typed or submitted."""

    async def record(page: Page) -> None:
        if rc.run_id in _LOGIN_RECORDED:
            return
        _LOGIN_RECORDED.add(rc.run_id)
        snap = await extract(
            page,
            ignore_selectors=cfg.ignore_selectors,
            fp_ignore="|".join(f"(?:{x})" for x in cfg.shell_toggle_patterns),
        )
        recorded = await rec.record_state(
            snap,
            kind="page",
            parent_state_id=None,
            source="login",
            depth=0,
            module="Sign in",
            nav_path=["Sign in"],
            title="Sign in",
        )
        if recorded.is_new and cfg.screenshots != "off":
            path, rel = rec.screenshot_path(recorded.state_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(path))
            await rec.set_screenshot(recorded.state_id, rel)

    return record


def make_explorer_models(
    rc: RunContext, cfg: CrawlConfig
) -> tuple[BaseChatModel, BaseChatModel]:
    """(planner, explorer) chat models. A seam: tests replace it with scripted models."""
    planner = get_chat_model(
        "supervisor", confidential=rc.project.confidential, scanner=rc.scanner
    )
    explorer = get_chat_model(
        "explorer",
        overrides={"explorer": settings.LLM_MODEL}
        if cfg.explorer_model == "strong"
        else None,
        confidential=rc.project.confidential,
        scanner=rc.scanner,
    )
    return planner, explorer


def _requirement_titles(project_id: uuid.UUID) -> list[str]:
    with Session(engine) as s:
        rows = s.exec(
            select(Requirement).where(
                Requirement.project_id == project_id,
                Requirement.review_status != "rejected",
            )
        ).all()
    return [f"{r.req_key}: {r.title}" for r in rows]


@dataclass
class RoleLogin:
    role_id: uuid.UUID
    name: str
    recipe: dict[str, Any]
    username: str
    password: str
    totp_seed: str | None
    storage_state: dict[str, Any] | None = None


def _load_roles(project_id: uuid.UUID, names: list[str] | None) -> list[RoleLogin]:
    with Session(engine) as s:
        stmt = (
            select(AppRole)
            .where(AppRole.project_id == project_id)
            .order_by(col(AppRole.name))
        )
        roles = [r for r in s.exec(stmt).all() if not names or r.name in names]
        missing = set(names or []) - {r.name for r in roles}
        if missing:
            raise ValueError(f"unknown role(s): {sorted(missing)}")
        out: list[RoleLogin] = []
        for r in roles:
            creds = load_role_credentials(s, r)  # raises AuthError if none stored
            out.append(
                RoleLogin(
                    r.id,
                    r.name,
                    dict(r.login_recipe),
                    creds["username"],
                    creds["password"],
                    creds.get("totp_seed"),
                    json.loads(creds["storage_state"])
                    if creds.get("storage_state")
                    else None,
                )
            )
        return out


def _create_snapshot(
    project_id: uuid.UUID, run_id: uuid.UUID, roles: list[str], stale_days: int
) -> uuid.UUID:
    with Session(engine) as s:
        snap = AppSnapshot(
            project_id=project_id,
            run_id=run_id,
            roles_covered=roles,
            stale_after=datetime.now(UTC) + timedelta(days=stale_days),
        )
        s.add(snap)
        s.commit()
        return snap.id


def _finish_snapshot(
    snapshot_id: uuid.UUID, run_id: uuid.UUID, status: str, stats: dict[str, Any]
) -> None:
    with Session(engine) as s:
        snap = s.get(AppSnapshot, snapshot_id)
        if snap:
            snap.status, snap.stats = status, stats
            s.add(snap)
        run = s.get(Run, run_id)
        if run:
            run.snapshot_id = snapshot_id
            s.add(run)
        s.commit()


async def run_crawl(rc: RunContext) -> dict[str, Any]:
    snapshot_cfg = rc.run.config_snapshot
    cfg = CrawlConfig(
        **{**rc.project.crawl_config, **snapshot_cfg.get("crawl_config", {})}
    )
    caps = BudgetCaps(
        **{**rc.project.budget_caps, **snapshot_cfg.get("budget_caps", {})}
    )
    role_names = snapshot_cfg.get("roles") or None
    roles = await asyncio.to_thread(_load_roles, rc.project.id, role_names)
    if not roles:
        raise ValueError(
            "this project has no roles with credentials; add one before crawling"
        )

    # Credentials stay in this process. Passwords/seeds are added to the scanner so they can never
    # appear in events, logs or LLM payloads even if a page echoes them.
    rc.scanner.add([x for r in roles for x in (r.password, r.totp_seed or "")])
    for (
        r
    ) in roles:  # a stored session is a bearer secret: mask its cookie/token values too
        for c in (r.storage_state or {}).get("cookies", []):
            rc.scanner.add([str(c.get("value", ""))])

    snapshot_id = await asyncio.to_thread(
        _create_snapshot,
        rc.project.id,
        rc.run.id,
        [r.name for r in roles],
        cfg.stale_after_days,
    )
    supervisor_id = str(uuid.uuid4())
    sup = AgentSpec(
        kind="supervisor",
        goal=(
            "supervised crawl: scouts map the structure, the supervisor splits the work and sizes the team"
            if cfg.explorer_mode == "agentic"
            else "deterministic crawl: one explorer per role"
        ),
        tools=[],
    )
    await rc.events.emit("agent_spawned", agent_id=supervisor_id, spec=sup.model_dump())
    await rc.events.emit(
        "agent_status",
        agent_id=supervisor_id,
        status="running",
        snapshot_id=str(snapshot_id),
        explorer_mode=cfg.explorer_mode,
    )

    artifact_dir = Path(settings.ARTIFACT_DIR).resolve()
    sem = asyncio.Semaphore(cfg.max_concurrent_agents)
    outcomes: list[dict[str, Any]] = []
    access_counts: dict[str, dict[str, int]] = {}
    live: dict[
        str, LiveRole
    ] = {}  # roles whose browser is still open for access probing

    async def explore_role(browser: Browser, role: RoleLogin) -> dict[str, Any]:
        async with sem:
            return await _explore_role(
                rc,
                browser,
                role,
                cfg,
                caps,
                snapshot_id,
                supervisor_id,
                artifact_dir,
                live,
            )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage"],  # Docker's /dev/shm is tiny
        )
        try:
            if cfg.explorer_mode == "agentic":
                outcomes, live = await _run_supervised(
                    rc,
                    browser,
                    roles,
                    cfg,
                    caps,
                    snapshot_id,
                    supervisor_id,
                    sup,
                    artifact_dir,
                )
            else:
                outcomes = list(
                    await asyncio.gather(*[explore_role(browser, r) for r in roles])
                )
            if cfg.probe_forbidden_routes and len(roles) > 1:
                access_counts = await _probe_access(rc, live, snapshot_id, cfg)
        finally:
            for lr in live.values():
                await lr.rb.close()
            await browser.close()

    statuses = [o["status"] for o in outcomes]
    run_status = status_from_agents(statuses)
    stats: dict[str, Any] = dict(
        await asyncio.to_thread(state_summary_sync, snapshot_id)
    )
    stats["skipped"] = sum(o["stats"].get("skipped", 0) for o in outcomes)
    stats["blocked"] = sum(o["stats"].get("blocked", 0) for o in outcomes)
    stats["roles"] = {o["role"]: o["status"] for o in outcomes}
    if access_counts:
        stats["access_probes"] = access_counts
    try:  # the graph is derived data: a failure is reported, never fatal
        stats["graph"] = await asyncio.to_thread(project_snapshot, snapshot_id)
    except Exception:
        logger.exception("graph projection failed")
        stats["graph"] = "failed"
    await asyncio.to_thread(
        _finish_snapshot,
        snapshot_id,
        rc.run.id,
        "ready" if run_status in {"succeeded", "stopped"} else "failed",
        stats,
    )
    await rc.events.emit(
        "agent_finished",
        agent_id=supervisor_id,
        status=run_status,
        result=f"{stats['states']} states, {stats['forms']} forms, {stats['fields']} fields",
        budget={},
    )
    return {
        "snapshot_id": str(snapshot_id),
        "statuses": statuses,
        "stats": stats,
        "partial": is_partial(statuses),
        "partial_reason": [
            o["detail"] for o in outcomes if o["status"] == "budget_exceeded"
        ],
    }


@dataclass
class LiveRole:
    role_id: uuid.UUID
    rb: RoleBrowser
    crawl: CrawlSession


async def _probe_access(
    rc: RunContext,
    live: dict[str, LiveRole],
    snapshot_id: uuid.UUID,
    cfg: CrawlConfig,
) -> dict[str, dict[str, int]]:
    """Open, as each role, the screens only other roles reached; record what came back."""
    from app.qa.crawler.access import probe_role

    out: dict[str, dict[str, int]] = {}
    for name, lr in sorted(live.items()):
        await rc.control.checkpoint()
        try:
            out[name] = await probe_role(
                lr.crawl, snapshot_id, lr.role_id, cfg.max_access_probes
            )
        except StopRequested:
            raise
        except Exception:
            logger.exception("access probing failed for role %s", name)
        if out.get(name):
            await rc.events.emit(
                "agent_status", status="access_probed", role=name, counts=out[name]
            )
    return out


async def _open_session(
    rc: RunContext,
    browser: Browser,
    role: RoleLogin,
    cfg: CrawlConfig,
    snapshot_id: uuid.UUID,
    artifact_dir: Path,
    owner_id: str,
) -> RoleSession:
    """Log a role in and give it a recorder. `rec.agent_id` always names the agent using the session."""
    policy = SafetyPolicy(
        allow_destructive=cfg.allow_destructive, allowlist=cfg.destructive_allowlist
    )
    rec = KnowledgeRecorder(
        snapshot_id=snapshot_id,
        project_id=rc.project.id,
        run_id=rc.run.id,
        role_id=role.role_id,
        role_name=role.name,
        agent_id=owner_id,
        events=rc.events,
        policy=policy,
        artifact_dir=artifact_dir,
        capture_text=cfg.capture_static_text,
    )
    seen_blocks: set[tuple[str, str]] = set()

    async def on_block(method: str, url: str, reason: str) -> None:
        parts = urlsplit(url)
        key = (method, f"{parts.netloc}{parts.path}")
        if key in seen_blocks:
            return
        seen_blocks.add(key)
        why = "external_request" if reason.startswith("external") else "blocked_request"
        await rc.events.emit(
            "blocked",
            agent_id=rec.agent_id,
            reason=why,
            method=method,
            url=f"{parts.netloc}{parts.path}",
        )
        await rec.log("blocked", why, url=url, action=method, source="network")

    async def human_code(prompt: str) -> str:
        code = await ask_human(
            rc.redis,
            rc.events,
            rc.control,
            run_id=rc.run_id,
            agent_id=rec.agent_id,
            role=role.name,
            prompt=prompt,
            timeout_s=HUMAN_CODE_TIMEOUT_S,
        )
        rc.scanner.add([code])
        return code

    async def on_session(what: str) -> None:
        await rc.events.emit(
            "agent_status", agent_id=rec.agent_id, status=what, role=role.name
        )
        await rec.log("crawled", what, action="login", source="session")

    rb = await RoleBrowser.open(
        browser,
        target_url=rc.project.target_base_url,
        role_name=role.name,
        recipe=role.recipe,
        username=role.username,
        password=role.password,
        on_block=on_block,
        safe_post_patterns=cfg.safe_post_patterns,
        ignore_selectors=cfg.ignore_selectors,
        fp_ignore="|".join(f"(?:{x})" for x in cfg.shell_toggle_patterns),
        totp_seed=role.totp_seed,
        human_code=human_code,
        storage_state=role.storage_state
        if (role.recipe.get("mfa") or "").lower() == "storage_state"
        else None,
        on_session=on_session,
        on_login_page=login_page_recorder(rc, rec, cfg),
    )
    crawl = CrawlSession(
        rb=rb,
        rec=rec,
        policy=policy,
        cfg=cfg,
        target_url=rc.project.target_base_url,
        role_name=role.name,
    )
    return RoleSession(role.name, role.role_id, rb, crawl, rec)


async def _run_supervised(
    rc: RunContext,
    browser: Browser,
    roles: list[RoleLogin],
    cfg: CrawlConfig,
    caps: BudgetCaps,
    snapshot_id: uuid.UUID,
    supervisor_id: str,
    sup_spec: AgentSpec,
    artifact_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, LiveRole]]:
    """Scouts -> supervisor plan -> explorer agents sized by complexity -> coverage replan."""
    by_name = {r.name: r for r in roles}
    ctx = AgentContext(
        run_id=rc.run_id,
        agent_id=supervisor_id,
        spec=sup_spec,
        budget=BudgetTracker(Budget(max_steps=10_000, max_tokens=1_000_000)),
        events=rc.events,
        control=rc.control,
        scanner=rc.scanner,
    )

    async def opener(role_name: str) -> RoleSession:
        return await _open_session(
            rc,
            browser,
            by_name[role_name],
            cfg,
            snapshot_id,
            artifact_dir,
            supervisor_id,
        )

    limits = {
        r.name: 1
        if (r.recipe.get("mfa") or "").lower() == "otp_human"
        else cfg.max_sessions_per_role
        for r in roles
    }
    pool = SessionPool(opener, limits)
    planner, explorer_model = make_explorer_models(rc, cfg)
    requirements = await asyncio.to_thread(_requirement_titles, rc.project.id)
    sup = Supervisor(
        ctx=ctx,
        gate=CRAWL_GATE,
        cfg=cfg,
        caps=caps,
        pool=pool,
        roles={r.name: r.role_id for r in roles},
        snapshot_id=snapshot_id,
        requirements=requirements,
        planner=planner,
        explorer=explorer_model,
    )
    summary: dict[str, Any] = {}
    status_error: str | None = None
    live: dict[str, LiveRole] = {}
    try:
        summary = await sup.run([r.name for r in roles])
    except StopRequested:
        summary = sup.summary([], 0)
        summary["roles"] = {r.name: "stopped" for r in roles}
    except Exception as exc:
        logger.exception("supervised crawl failed")
        status_error = f"{type(exc).__name__}: {str(exc)[:300]}"
        await rc.events.emit("error", agent_id=supervisor_id, message=status_error)
        summary = sup.summary([], 0)
        summary["roles"] = {r.name: "failed" for r in roles}
    finally:
        for r in roles:  # the first session of each role stays open for access probing
            prim = pool.primary(r.name)
            if prim is not None and summary.get("roles", {}).get(r.name) != "failed":
                live[r.name] = LiveRole(r.role_id, prim.rb, prim.crawl)
        pool_sessions = pool.created
        for s in pool_sessions:
            if s is not pool.primary(s.role):
                await s.rb.close()
    outcomes: list[dict[str, Any]] = []
    for r in roles:
        mine = [s for s in pool_sessions if s.role == r.name]
        outcomes.append(
            {
                "role": r.name,
                "status": summary.get("roles", {}).get(r.name, "succeeded"),
                "detail": summary.get("role_detail", {}).get(r.name, "")
                or (status_error or ""),
                "stats": {
                    "skipped": sum(s.rec.stats.skipped for s in mine),
                    "blocked": sum(s.rec.stats.blocked for s in mine),
                },
            }
        )
    rc_summary = {k: v for k, v in summary.items() if k in {"packages", "replans"}}
    rc.run.summary = {**rc.run.summary, "supervisor": rc_summary}
    return outcomes, live


async def _explore_role(
    rc: RunContext,
    browser: Browser,
    role: RoleLogin,
    cfg: CrawlConfig,
    caps: BudgetCaps,
    snapshot_id: uuid.UUID,
    supervisor_id: str,
    artifact_dir: Path,
    live: dict[str, LiveRole],
) -> dict[str, Any]:
    tools = [t.name for t in BROWSER_TOOLS]
    spec = validate_spec(
        AgentSpec(
            kind="explorer",
            goal=f"Explore the application as the '{role.name}' role",
            scope=Scope(role_ref=role.name, max_depth=cfg.max_depth),
            tools=tools,
            budget=Budget(
                max_steps=cfg.max_steps_per_agent,
                max_seconds=caps.max_seconds_per_agent,
            ),
        ),
        caps,
        known_roles={role.name},
    )
    agent_id = str(uuid.uuid4())
    ctx = AgentContext(
        run_id=rc.run_id,
        agent_id=agent_id,
        parent_id=supervisor_id,
        spec=spec,
        budget=BudgetTracker(spec.budget),
        events=rc.events,
        control=rc.control,
        scanner=rc.scanner,
    )
    await rc.events.emit(
        "agent_spawned",
        agent_id=agent_id,
        parent_id=supervisor_id,
        spec=spec.model_dump(),
        role=role.name,
    )
    await rc.events.emit(
        "agent_status", agent_id=agent_id, status="running", role=role.name
    )
    policy = SafetyPolicy(
        allow_destructive=cfg.allow_destructive, allowlist=cfg.destructive_allowlist
    )
    rec = KnowledgeRecorder(
        snapshot_id=snapshot_id,
        project_id=rc.project.id,
        run_id=rc.run.id,
        role_id=role.role_id,
        role_name=role.name,
        agent_id=agent_id,
        events=rc.events,
        policy=policy,
        artifact_dir=artifact_dir,
        capture_text=cfg.capture_static_text,
    )

    seen_blocks: set[tuple[str, str]] = set()

    async def on_block(method: str, url: str, reason: str) -> None:
        # One entry per (method, path): a page that polls would otherwise write thousands of rows.
        parts = urlsplit(url)
        key = (method, f"{parts.netloc}{parts.path}")
        if key in seen_blocks:
            return
        seen_blocks.add(key)
        why = "external_request" if reason.startswith("external") else "blocked_request"
        await rc.events.emit(
            "blocked",
            agent_id=agent_id,
            reason=why,
            method=method,
            url=f"{parts.netloc}{parts.path}",
        )
        await rec.log("blocked", why, url=url, action=method, source="network")

    async def human_code(prompt: str) -> str:
        code = await ask_human(
            rc.redis,
            rc.events,
            rc.control,
            run_id=rc.run_id,
            agent_id=agent_id,
            role=role.name,
            prompt=prompt,
            timeout_s=HUMAN_CODE_TIMEOUT_S,
        )
        rc.scanner.add([code])
        return code

    async def on_session(what: str) -> None:
        await rc.events.emit(
            "agent_status", agent_id=agent_id, status=what, role=role.name
        )
        await rec.log("crawled", what, action="login", source="session")

    status, detail = "succeeded", ""
    stats: dict[str, Any] = {}
    rb: RoleBrowser | None = None
    try:
        await rc.control.checkpoint(agent_id)
        rb = await RoleBrowser.open(
            browser,
            target_url=rc.project.target_base_url,
            role_name=role.name,
            recipe=role.recipe,
            username=role.username,
            password=role.password,
            on_block=on_block,
            safe_post_patterns=cfg.safe_post_patterns,
            ignore_selectors=cfg.ignore_selectors,
            fp_ignore="|".join(f"(?:{x})" for x in cfg.shell_toggle_patterns),
            totp_seed=role.totp_seed,
            human_code=human_code,
            storage_state=role.storage_state
            if (role.recipe.get("mfa") or "").lower() == "storage_state"
            else None,
            on_session=on_session,
            on_login_page=login_page_recorder(rc, rec, cfg),
        )
        await rc.events.emit(
            "agent_status", agent_id=agent_id, status="authenticated", role=role.name
        )
        crawl = CrawlSession(
            rb=rb,
            rec=rec,
            policy=policy,
            cfg=cfg,
            target_url=rc.project.target_base_url,
            role_name=role.name,
        )
        ctx.resources["crawl"] = crawl
        live[role.name] = LiveRole(role.role_id, rb, crawl)
        stats = await BfsExplorer(ctx, CRAWL_GATE, crawl).run()
    except StopRequested:
        status = "stopped"
    except BudgetExceeded as exc:
        status, detail = "budget_exceeded", exc.which
    except AuthError as exc:
        status, detail = "failed", f"login failed: {exc}"
        await rc.events.emit("error", agent_id=agent_id, message=detail)
        await rec.log("error", "auth", action="login")
    except Exception as exc:
        logger.exception("explorer for role %s failed", role.name)
        status, detail = "failed", f"{type(exc).__name__}: {str(exc)[:300]}"
        await rc.events.emit("error", agent_id=agent_id, message=detail)
    finally:
        # a browser that explored fine stays open: the access-probing phase reuses its session
        if rb is not None and (
            status not in {"succeeded", "budget_exceeded"}
            or not cfg.probe_forbidden_routes
        ):
            live.pop(role.name, None)
            await rb.close()
        stats = stats or rec.stats.as_dict()
    await rc.events.emit(
        "agent_finished",
        agent_id=agent_id,
        status=status,
        detail=detail,
        result=f"{stats.get('states', 0)} states",
        budget=ctx.budget.snapshot(),
    )
    return {"role": role.name, "status": status, "stats": stats, "detail": detail}
