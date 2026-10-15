"""`crawl` run: explore the target application once per role and build a knowledge-model snapshot."""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Browser, async_playwright
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.db import engine
from app.qa.agents.budget import BudgetExceeded, BudgetTracker
from app.qa.agents.spec import AgentSpec, Budget, BudgetCaps, Scope, validate_spec
from app.qa.agents.tools.browser_tools import BROWSER_TOOLS
from app.qa.browser.auth import AuthError, load_role_credentials
from app.qa.browser.session import RoleBrowser
from app.qa.config import CrawlConfig
from app.qa.crawler.bfs import BfsExplorer
from app.qa.crawler.session import CrawlSession
from app.qa.gate.gate import AgentContext, ToolGate
from app.qa.gate.safety import SafetyPolicy
from app.qa.knowledge.recorder import KnowledgeRecorder, state_summary_sync
from app.qa.models import AppRole, AppSnapshot, Run
from app.qa.runs.context import RunContext
from app.qa.runs.control import StopRequested
from app.qa.runs.status import is_partial, status_from_agents

logger = logging.getLogger(__name__)

CRAWL_GATE = ToolGate(BROWSER_TOOLS)


@dataclass
class RoleLogin:
    role_id: uuid.UUID
    name: str
    recipe: dict[str, Any]
    username: str
    password: str
    totp_seed: str | None


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

    snapshot_id = await asyncio.to_thread(
        _create_snapshot,
        rc.project.id,
        rc.run.id,
        [r.name for r in roles],
        cfg.stale_after_days,
    )
    supervisor_id = str(uuid.uuid4())
    sup = AgentSpec(
        kind="supervisor", goal="deterministic crawl: one explorer per role", tools=[]
    )
    await rc.events.emit("agent_spawned", agent_id=supervisor_id, spec=sup.model_dump())
    await rc.events.emit(
        "agent_status",
        agent_id=supervisor_id,
        status="running",
        snapshot_id=str(snapshot_id),
        explorer_mode="deterministic",
    )

    artifact_dir = Path(settings.ARTIFACT_DIR).resolve()
    sem = asyncio.Semaphore(cfg.max_concurrent_agents)
    outcomes: list[dict[str, Any]] = []

    async def explore_role(browser: Browser, role: RoleLogin) -> dict[str, Any]:
        async with sem:
            return await _explore_role(
                rc, browser, role, cfg, caps, snapshot_id, supervisor_id, artifact_dir
            )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage"],  # Docker's /dev/shm is tiny
        )
        try:
            outcomes = list(
                await asyncio.gather(*[explore_role(browser, r) for r in roles])
            )
        finally:
            await browser.close()

    statuses = [o["status"] for o in outcomes]
    run_status = status_from_agents(statuses)
    stats: dict[str, Any] = dict(
        await asyncio.to_thread(state_summary_sync, snapshot_id)
    )
    stats["skipped"] = sum(o["stats"].get("skipped", 0) for o in outcomes)
    stats["blocked"] = sum(o["stats"].get("blocked", 0) for o in outcomes)
    stats["roles"] = {o["role"]: o["status"] for o in outcomes}
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


async def _explore_role(
    rc: RunContext,
    browser: Browser,
    role: RoleLogin,
    cfg: CrawlConfig,
    caps: BudgetCaps,
    snapshot_id: uuid.UUID,
    supervisor_id: str,
    artifact_dir: Path,
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
        if rb is not None:
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
