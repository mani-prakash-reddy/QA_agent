"""Push a project's test cases to the user's connected tool, creating new ones and updating the ones pushed
before (never duplicating), with a dry run that shows what would happen."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, col, select

from app.qa.integrations.base import CasePayload, Connector, IntegrationError
from app.qa.integrations.jira import Jira
from app.qa.integrations.testrail import TestRail
from app.qa.integrations.zephyr import Zephyr
from app.qa.models import (
    Requirement,
    TestCase,
    TestCaseExternal,
    UserIntegration,
)
from app.qa.security.crypto import decrypt_json

PLATFORMS: dict[str, type[Connector]] = {
    "jira": Jira,
    "testrail": TestRail,
    "zephyr": Zephyr,
}
MAX_CASES = 300
CONCURRENCY = 4


def connector_for(integ: UserIntegration, client: Any = None) -> Connector:
    secret = str(decrypt_json(integ.ciphertext).get("secret", ""))
    return PLATFORMS[integ.platform](
        integ.base_url, integ.username, secret, integ.settings, client
    )


def payload_of(t: TestCase, req_key: str) -> CasePayload:
    return CasePayload(
        tc_key=t.tc_key,
        title=t.title,
        requirement=req_key,
        priority=t.priority,
        test_type=t.test_type,
        module=t.module or "",
        role=t.role_name or "",
        preconditions=[str(p) for p in t.preconditions],
        steps=[str(s) for s in t.rendered_steps],
        expected=t.expected_result,
        basis=str(t.evidence.get("basis", "inferred")),
    )


def content_hash(p: CasePayload) -> str:
    return hashlib.sha256(json.dumps(asdict(p), sort_keys=True).encode()).hexdigest()


def push_cases(
    session: Session,
    integ: UserIntegration,
    project_id: Any,
    destination: dict[str, Any],
    *,
    dry_run: bool,
    include_drafts: bool,
    case_ids: list[Any] | None = None,
    client: Any = None,
) -> dict[str, Any]:
    conn = connector_for(integ, client)
    dest_key = conn.destination_key(destination)
    stmt = select(TestCase).where(TestCase.project_id == project_id)
    stmt = stmt.where(
        col(TestCase.status).in_(
            ["approved", "edited", "draft"] if include_drafts else ["approved"]
        )
    )
    if case_ids:
        stmt = stmt.where(col(TestCase.id).in_(case_ids))
    cases = list(session.exec(stmt.order_by(col(TestCase.tc_key))).all())
    reqs = {
        r.id: r.req_key
        for r in session.exec(
            select(Requirement).where(Requirement.project_id == project_id)
        )
    }
    existing = {
        e.test_case_id: e
        for e in session.exec(
            select(TestCaseExternal).where(
                col(TestCaseExternal.test_case_id).in_([c.id for c in cases] or [None]),
                TestCaseExternal.platform == integ.platform,
                TestCaseExternal.destination == dest_key,
            )
        )
    }
    if len(cases) > MAX_CASES:
        raise IntegrationError(
            f"push at most {MAX_CASES} cases at a time (selected {len(cases)})"
        )

    plan: list[tuple[TestCase, CasePayload, str, str, TestCaseExternal | None]] = []
    for c in cases:
        p = payload_of(c, reqs.get(c.requirement_id, ""))
        h = content_hash(p)
        ex = existing.get(c.id)
        action = (
            "create"
            if ex is None
            else ("unchanged" if ex.content_hash == h else "update")
        )
        plan.append((c, p, h, action, ex))

    rows: list[dict[str, Any]] = []
    todo = [x for x in plan if x[3] != "unchanged"]
    outcome: dict[str, Any] = {}

    def run(
        item: tuple[TestCase, CasePayload, str, str, TestCaseExternal | None],
    ) -> tuple[str, Any]:
        c, p, _h, action, ex = item
        try:
            if action == "create":
                return c.tc_key, conn.create(destination, p)
            assert ex is not None
            return c.tc_key, conn.update(destination, ex.external_id, p)
        except IntegrationError as exc:
            return c.tc_key, exc

    try:
        if not dry_run and todo:
            with ThreadPoolExecutor(CONCURRENCY) as pool:
                outcome = dict(pool.map(run, todo))
    finally:
        conn.close()

    now = datetime.now(UTC)
    for c, _p, h, action, ex in plan:
        row: dict[str, Any] = {
            "tc_key": c.tc_key,
            "title": c.title,
            "action": action,
            "external_id": ex.external_id if ex else None,
            "url": ex.external_url if ex else None,
            "error": None,
        }
        res = outcome.get(c.tc_key)
        if isinstance(res, IntegrationError):
            row["action"], row["error"] = "failed", str(res)
        elif res is not None:
            row.update(external_id=res.external_id, url=res.url)
            if ex is None:
                session.add(
                    TestCaseExternal(
                        test_case_id=c.id,
                        platform=integ.platform,
                        destination=dest_key,
                        external_id=res.external_id,
                        external_url=res.url,
                        content_hash=h,
                    )
                )
            else:
                ex.external_url, ex.content_hash, ex.pushed_at = res.url, h, now
                session.add(ex)
            row["action"] = "created" if action == "create" else "updated"
        elif dry_run and action != "unchanged":
            row["action"] = f"would {action}"
        rows.append(row)
    session.commit()
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    return {
        "platform": integ.platform,
        "destination": dest_key,
        "dry_run": dry_run,
        "counts": counts,
        "cases": rows,
    }
