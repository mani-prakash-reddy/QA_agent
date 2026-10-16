"""Traceability matrix, exports (test cases, RTM, gaps, knowledge model), the test-case template and run metrics."""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.api.routes.mapping import _current_snapshot, _gaps
from app.api.routes.projects import get_owned_project
from app.qa.config import TEMPLATE_COLUMNS, GenerationConfig, TemplateColumn
from app.qa.exports import build, files
from app.qa.models import Run
from app.qa.schemas import (
    KindMetric,
    MetricsPublic,
    RtmPublic,
    RtmRow,
    RunMetric,
    TemplateColumnPublic,
    TemplatePublic,
    TemplateUpdate,
)

router = APIRouter(tags=["exports"])


@router.get("/projects/{project_id}/rtm", response_model=RtmPublic)
def read_rtm(
    session: SessionDep, current_user: CurrentUser, project_id: uuid.UUID
) -> Any:
    project = get_owned_project(session, current_user, project_id)
    rows = build.rtm(session, project)
    live = [r for r in rows if r["review_status"] != "rejected"]
    return RtmPublic(
        rows=[RtmRow(**r) for r in rows],
        requirements=len(live),
        covered=sum(1 for r in live if r["coverage"] == "covered"),
        uncovered=sum(1 for r in live if r["coverage"] == "uncovered"),
        needs_decision=sum(1 for r in live if r["needs_decision"]),
        test_cases=sum(r["tests_own"] for r in rows),
    )


@router.get("/projects/{project_id}/exports/{kind}")
def export(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    kind: str,
    format: str = Query(default="xlsx", pattern="^(csv|json|xlsx)$"),
    status: str | None = None,
    test_type: str | None = None,
    include_rejected: bool = False,
    snapshot_id: uuid.UUID | None = None,
) -> Response:
    project = get_owned_project(session, current_user, project_id)
    if kind == "tests":
        sheets = {
            "Test cases": build.test_sheet(
                session,
                project,
                status=status,
                test_type=test_type,
                include_rejected=include_rejected,
            )
        }
    elif kind == "rtm":
        sheets = {"RTM": build.rtm_sheet(build.rtm(session, project))}
    elif kind == "gaps":
        snap = _current_snapshot(session, project_id, snapshot_id)
        sheets = {"Gaps": build.gap_sheet(_gaps(session, project_id, snap, None, None))}
    elif kind == "km":
        snap = _current_snapshot(session, project_id, snapshot_id)
        if snap is None:
            raise HTTPException(
                status_code=409, detail="There is no crawl to export yet."
            )
        sheets = build.km_sheets(session, snap)
    else:
        raise HTTPException(status_code=404, detail="Unknown export")
    data, media = files.render(format, sheets)
    name = {
        "tests": "test-cases",
        "rtm": "rtm",
        "gaps": "gap-report",
        "km": "knowledge-model",
    }[kind]
    return Response(
        data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}.{format}"'},
    )


def _template_public(project: Any) -> TemplatePublic:
    tpl = build.template_of(project)
    return TemplatePublic(
        columns=[TemplateColumnPublic(key=c.key, header=c.header) for c in tpl.columns],
        id_prefix=tpl.id_prefix,
        available=[
            TemplateColumnPublic(key=k, header=h) for k, h in TEMPLATE_COLUMNS.items()
        ],
    )


@router.get("/projects/{project_id}/template", response_model=TemplatePublic)
def read_template(
    session: SessionDep, current_user: CurrentUser, project_id: uuid.UUID
) -> Any:
    return _template_public(get_owned_project(session, current_user, project_id))


@router.put("/projects/{project_id}/template", response_model=TemplatePublic)
def update_template(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    body: TemplateUpdate,
) -> Any:
    project = get_owned_project(session, current_user, project_id)
    keys = [c.key for c in body.columns]
    if len(set(keys)) != len(keys):
        raise HTTPException(status_code=422, detail="A column can appear only once")
    unknown = [k for k in keys if k not in TEMPLATE_COLUMNS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown column: {unknown[0]}")
    cfg = GenerationConfig.model_validate(project.generation_config or {})
    cfg.template.columns = [
        TemplateColumn(key=c.key, header=c.header) for c in body.columns
    ]
    cfg.template.id_prefix = body.id_prefix
    project.generation_config = cfg.model_dump(mode="json")
    session.add(project)
    session.commit()
    session.refresh(project)
    return _template_public(project)


@router.get("/projects/{project_id}/metrics", response_model=MetricsPublic)
def metrics(
    session: SessionDep, current_user: CurrentUser, project_id: uuid.UUID
) -> Any:
    get_owned_project(session, current_user, project_id)
    runs = session.exec(
        select(Run)
        .where(Run.project_id == project_id)
        .order_by(col(Run.created_at).desc())
    ).all()
    out: list[RunMetric] = []
    agg: dict[str, KindMetric] = {}
    for r in runs:
        secs = (
            (r.finished_at - r.started_at).total_seconds()
            if r.started_at and r.finished_at
            else None
        )
        tin = int(r.summary.get("input_tokens") or 0)
        tout = int(r.summary.get("output_tokens") or 0)
        out.append(
            RunMetric(
                run_id=r.id,
                kind=r.kind,
                status=r.status,
                seconds=secs,
                input_tokens=tin,
                output_tokens=tout,
                created_at=r.created_at,
            )
        )
        k = agg.setdefault(
            r.kind,
            KindMetric(
                kind=r.kind, runs=0, seconds=0.0, input_tokens=0, output_tokens=0
            ),
        )
        k.runs += 1
        k.seconds += secs or 0.0
        k.input_tokens += tin
        k.output_tokens += tout
    return MetricsPublic(
        by_kind=sorted(agg.values(), key=lambda k: k.kind),
        runs=out[:50],
        input_tokens=sum(k.input_tokens for k in agg.values()),
        output_tokens=sum(k.output_tokens for k in agg.values()),
    )
