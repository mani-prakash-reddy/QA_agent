"""Forbidden-route probing: open, by address, the screens other roles reached and this role did not.

What the role gets (the screen, a redirect, a 403/404, a "no permission" page) is recorded against the
screen as observed access, so a "role X cannot open Y" test rests on an observation, not on a guess.
Read-only: it only navigates, behind the same network guard as the crawl."""

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, col, select

from app.core.db import engine
from app.qa.browser.extract import route_path
from app.qa.browser.session import ActionError
from app.qa.crawler.session import CrawlSession
from app.qa.models import RoleStateAccess, UiState

DENIED_TEXT = re.compile(
    r"\b(forbidden|unauthori[sz]ed|access denied|permission denied|not authori[sz]ed|"
    r"no permission|not allowed|(do not|don'?t) have (access|permission)|403)\b",
    re.I,
)
MISSING_TEXT = re.compile(
    r"\b(not found|404|page (does not|doesn'?t) exist|no such page)\b", re.I
)


@dataclass
class Probe:
    state_id: uuid.UUID
    url: str
    route: str
    title: str


def candidates(snapshot_id: uuid.UUID, role_id: uuid.UUID, limit: int) -> list[Probe]:
    """Pages some other role reached that this role never did, one per route."""
    with Session(engine) as s:
        mine = {
            a.state_id
            for a in s.exec(
                select(RoleStateAccess).where(
                    RoleStateAccess.snapshot_id == snapshot_id,
                    RoleStateAccess.role_id == role_id,
                )
            )
        }
        seen_elsewhere = {
            a.state_id
            for a in s.exec(
                select(RoleStateAccess).where(
                    RoleStateAccess.snapshot_id == snapshot_id,
                    RoleStateAccess.role_id != role_id,
                    RoleStateAccess.access == "visible",
                )
            )
        }
        states = s.exec(
            select(UiState)
            .where(
                UiState.snapshot_id == snapshot_id,
                UiState.kind == "page",
                col(UiState.id).in_(seen_elsewhere - mine)
                if seen_elsewhere - mine
                else False,
            )
            .order_by(col(UiState.depth), col(UiState.title))
        ).all()
    out: list[Probe] = []
    routes: set[str] = set()
    for st in states:
        if not st.sample_url or st.route_template in routes:
            continue
        routes.add(st.route_template or st.sample_url)
        out.append(Probe(st.id, st.sample_url, st.route_template or "", st.title))
        if len(out) >= limit:
            break
    return out


def classify(
    status: int | None,
    requested: str,
    final: str,
    heading: str,
    text: str,
    login_route: str,
) -> tuple[str, str]:
    """-> (access, evidence). `visible` means the requested screen was served."""
    if status in {401, 403}:
        return "forbidden", f"HTTP {status}"
    if status == 404:
        return "not_found", "HTTP 404"
    if route_path(final) == login_route and route_path(requested) != login_route:
        return "redirected", "sent to the login page"
    if route_path(final) != route_path(requested):
        return "redirected", f"redirected to {route_path(final)}"
    shown = f"{heading} {text}"[:700]
    if DENIED_TEXT.search(shown):
        return "forbidden", "the page says access is denied"
    if MISSING_TEXT.search(f"{heading} {text[:200]}"):
        return "not_found", "the page says it was not found"
    return "visible", "the screen was served"


async def probe_role(
    crawl: CrawlSession, snapshot_id: uuid.UUID, role_id: uuid.UUID, limit: int
) -> dict[str, Any]:
    rb, rec = crawl.rb, crawl.rec
    login_route = rb._login_route() if rb.login_ctx else "/login"  # noqa: SLF001
    counts: dict[str, int] = {}
    for probe in candidates(snapshot_id, role_id, limit):
        ok, why = crawl.path_allowed(probe.url)
        if not ok or crawl.policy.classify(probe.title, probe.url).blocked:
            continue
        try:
            await rb.goto(probe.url)
            snap = await rb.snapshot()
            text = await rb.page.evaluate(
                "() => (document.body && document.body.innerText || '').slice(0, 500)"
            )
        except ActionError as exc:
            await rec.log(
                "skipped", "access_probe_failed", url=probe.url, label=str(exc)[:200]
            )
            continue
        access, evidence = classify(
            rb.last_status,
            probe.url,
            snap.url,
            snap.heading or snap.title,
            str(text),
            login_route,
        )
        counts[access] = counts.get(access, 0) + 1
        if access == "visible":
            # served: the role can open it (record what it really got; a catch-all page differs)
            obs = await crawl.observe(snap, source="probe")
            same = snap.fingerprint == await _fingerprint(probe.state_id)
            await rec.record_access(
                probe.state_id if same else obs.state_id,
                "visible",
                "opened by address",
            )
        else:
            await rec.record_access(probe.state_id, access, evidence)
        await rec.log(
            "crawled",
            f"access:{access}",
            url=probe.url,
            action="navigate",
            label=probe.title,
            source="access_probe",
            state_id=probe.state_id,
        )
    return counts


async def _fingerprint(state_id: uuid.UUID) -> str | None:
    with Session(engine) as s:
        st = s.get(UiState, state_id)
        return st.fingerprint if st else None
