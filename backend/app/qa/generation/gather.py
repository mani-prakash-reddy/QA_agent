"""Collect the facts test generation may use: forms (with how to reach them) and role access."""

import uuid
from dataclasses import dataclass, field

from sqlmodel import Session, col, select

from app.qa.mapping.paths import PathPlanner
from app.qa.models import (
    AppRole,
    AppSnapshot,
    FormField,
    RoleStateAccess,
    Transition,
    UiForm,
    UiState,
)


@dataclass
class FormCtx:
    state: UiState
    form: UiForm
    fields: list[FormField]
    path: list[tuple[str, str]]  # clicks from the start page: [(label, element kind)]
    start_title: str
    parent_title: str
    parent_state_id: uuid.UUID
    needs_rows: bool = (
        False  # a click on the path is a per-row control: a row must exist first
    )

    @property
    def opener(self) -> str | None:
        return self.path[-1][0] if self.path else None


MAX_DEPTH = 3  # screen -> menu -> dialog is the deepest the crawler records in practice


def forms_for_states(
    session: Session,
    snapshot_id: uuid.UUID,
    state_ids: list[uuid.UUID],
    planner: PathPlanner,
) -> tuple[list[FormCtx], int]:
    """Forms on the mapped screens and on overlays (menus, dialogs, tabs) opened from them, each
    with the planned click path. Returns (forms, unreachable): a form whose path cannot be planned
    (an opener with no accessible name was never recorded) yields no cases rather than a wrong one."""
    mapped = {
        s.id: s
        for s in session.exec(select(UiState).where(col(UiState.id).in_(state_ids)))
    }
    owner: dict[uuid.UUID, UiState] = dict(mapped)
    frontier = list(mapped)
    for _ in range(MAX_DEPTH):
        nxt: list[uuid.UUID] = []
        for t in session.exec(
            select(Transition).where(
                Transition.snapshot_id == snapshot_id,
                col(Transition.from_state_id).in_(frontier),
                col(Transition.kind).in_(["overlay", "tab"]),
            )
        ):
            if t.to_state_id not in owner:
                owner[t.to_state_id] = owner[t.from_state_id]
                nxt.append(t.to_state_id)
        frontier = nxt
        if not frontier:
            break
    states = {
        st.id: st
        for st in session.exec(select(UiState).where(col(UiState.id).in_(list(owner))))
    }
    out: list[FormCtx] = []
    unreachable = 0
    seen_forms: set[uuid.UUID] = set()
    for sid, root in owner.items():
        for form in session.exec(select(UiForm).where(UiForm.state_id == sid)):
            if form.id in seen_forms:
                continue
            seen_forms.add(form.id)
            planned = planner.plan(sid)
            if planned is None:
                unreachable += 1
                continue
            fields = list(
                session.exec(
                    select(FormField)
                    .where(FormField.form_id == form.id)
                    .order_by(col(FormField.ordinal))
                )
            )
            out.append(
                FormCtx(
                    state=states[sid],
                    form=form,
                    fields=fields,
                    path=planned[0],
                    start_title=planner.start_title,
                    parent_title=root.heading or root.title,
                    parent_state_id=root.id,
                    needs_rows=planned[1],
                )
            )
    return out, unreachable


@dataclass
class RoleAccess:
    roles: dict[uuid.UUID, str] = field(default_factory=dict)
    visible: dict[uuid.UUID, set[uuid.UUID]] = field(
        default_factory=dict
    )  # state -> roles
    denied: dict[uuid.UUID, set[uuid.UUID]] = field(
        default_factory=dict
    )  # state -> roles
    seen_any: set[uuid.UUID] = field(default_factory=set)  # roles that crawled at all
    complete: set[uuid.UUID] = field(default_factory=set)  # roles whose crawl finished


def role_access(
    session: Session, snapshot_id: uuid.UUID, project_id: uuid.UUID
) -> RoleAccess:
    ra = RoleAccess()
    snap = session.get(AppSnapshot, snapshot_id)
    statuses: dict[str, str] = (snap.stats.get("roles") or {}) if snap else {}
    ra.roles = {
        r.id: r.name
        for r in session.exec(select(AppRole).where(AppRole.project_id == project_id))
    }
    ra.complete = {
        rid for rid, name in ra.roles.items() if statuses.get(name) == "succeeded"
    }
    for a in session.exec(
        select(RoleStateAccess).where(RoleStateAccess.snapshot_id == snapshot_id)
    ):
        ra.seen_any.add(a.role_id)
        bucket = ra.visible if a.access == "visible" else ra.denied
        bucket.setdefault(a.state_id, set()).add(a.role_id)
    return ra
