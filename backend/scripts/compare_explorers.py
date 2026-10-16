"""Run the deterministic and the agentic explorer on the same project and compare what each found.

    uv run python scripts/compare_explorers.py "<project>" [--modes deterministic,agentic] [--max-depth N]
        [--steps N] [--base-url URL] > comparison.json

Executes each crawl in-process (like the worker would). Needs the roles' credentials in the project and
the target reachable from this machine. Output: per mode the screens, forms, fields, transitions,
agent steps, tokens, seconds, and the screens found by one mode only."""

import argparse
import asyncio
import json
import sys
from typing import Any

from redis.asyncio import Redis
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.db import engine
from app.qa.models import (
    AgentEvent,
    AgentRun,
    AppSnapshot,
    Project,
    Run,
    UiState,
)
from app.qa.runs.executor import execute_run


async def one(project_id: Any, mode: str, extra: dict[str, Any]) -> dict[str, Any]:
    with Session(engine) as s:
        run = Run(
            project_id=project_id,
            kind="crawl",
            config_snapshot={"crawl_config": {"explorer_mode": mode, **extra}},
        )
        s.add(run)
        s.commit()
        run_id = run.id
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        status = await execute_run(run_id, redis)
    finally:
        await redis.aclose()
    with Session(engine) as s:
        run = s.get(Run, run_id)
        assert run
        snap = s.exec(select(AppSnapshot).where(AppSnapshot.run_id == run_id)).first()
        agents = list(s.exec(select(AgentRun).where(AgentRun.run_id == run_id)))
        usage = [
            e.payload
            for e in s.exec(
                select(AgentEvent).where(
                    AgentEvent.run_id == run_id, col(AgentEvent.type) == "llm_usage"
                )
            )
        ]
        states = (
            list(s.exec(select(UiState).where(UiState.snapshot_id == snap.id)))
            if snap
            else []
        )
        secs = (
            (run.finished_at - run.started_at).total_seconds()
            if run.started_at and run.finished_at
            else None
        )
        stats = snap.stats if snap else {}
        return {
            "mode": mode,
            "status": status,
            "seconds": round(secs, 1) if secs else None,
            "screens": stats.get("states"),
            "forms": stats.get("forms"),
            "fields": stats.get("fields"),
            "transitions": stats.get("transitions"),
            "agents": len(agents),
            "tool_steps": sum(a.steps for a in agents),
            "llm_calls": len(usage),
            "tokens": sum(
                int(u.get("input_tokens", 0)) + int(u.get("output_tokens", 0))
                for u in usage
            ),
            "roles": stats.get("roles"),
            "screen_titles": sorted(
                {f"{st.kind}:{st.title}" for st in states if st.kind != "menu"}
            ),
            "error": run.error,
        }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--modes", default="deterministic,agentic")
    ap.add_argument("--max-depth", type=int)
    ap.add_argument("--steps", type=int)
    ap.add_argument("--base-url")
    a = ap.parse_args()
    extra: dict[str, Any] = {}
    if a.max_depth is not None:
        extra["max_depth"] = a.max_depth
    if a.steps is not None:
        extra["max_steps_per_agent"] = a.steps
    with Session(engine) as s:
        p = s.exec(select(Project).where(Project.name == a.project)).one()
        if a.base_url:
            p.target_base_url = a.base_url
            s.add(p)
            s.commit()
        pid = p.id
    results = [await one(pid, m, extra) for m in a.modes.split(",")]
    if len(results) == 2:
        x, y = (set(r["screen_titles"]) for r in results)
        results[0]["only_here"] = sorted(x - y)
        results[1]["only_here"] = sorted(y - x)
    sys.stdout.write(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
