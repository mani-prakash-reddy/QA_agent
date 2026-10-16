"""Persists what the crawler observes into the knowledge model and emits the matching live events."""

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, select

from app.core.db import engine
from app.qa.browser.extract import Element, Snapshot, clean_label, mask_pii
from app.qa.events import EventPublisher
from app.qa.gate.safety import SafetyPolicy
from app.qa.models import (
    CrawlEvent,
    FormField,
    RoleStateAccess,
    Transition,
    UiElement,
    UiForm,
    UiState,
)

FORM_INPUT_KINDS = {
    "input",
    "textarea",
    "select",
    "combobox",
    "checkbox",
    "radio",
    "switch",
}


@dataclass
class RecordedState:
    state_id: uuid.UUID
    is_new: bool
    fingerprint: str
    # live handle -> form_field id, for attaching probe results
    field_ids: dict[str, uuid.UUID] = field(default_factory=dict)
    # element key -> form_field id (stable across snapshots, unlike handles)
    field_ids_by_key: dict[str, uuid.UUID] = field(default_factory=dict)


@dataclass
class Stats:
    states: int = 0
    forms: int = 0
    fields: int = 0
    transitions: int = 0
    skipped: int = 0
    blocked: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


def _locators(e: Element) -> list[dict[str, Any]]:
    """Ordered candidate locators for later replay: semantic first, structural last."""
    out: list[dict[str, Any]] = []
    role = e.role or {
        "link": "link",
        "button": "button",
        "input": "textbox",
        "textarea": "textbox",
        "select": "combobox",
        "checkbox": "checkbox",
        "tab": "tab",
    }.get(e.kind)
    if role and e.label:
        out.append({"by": "role", "role": role, "name": e.label})
    if e.name_attr:
        out.append({"by": "css", "value": f'[name="{e.name_attr}"]'})
    if e.href:
        out.append({"by": "css", "value": f'a[href="{e.href}"]'})
    return out


class KnowledgeRecorder:
    def __init__(
        self,
        *,
        snapshot_id: uuid.UUID,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        role_id: uuid.UUID,
        role_name: str,
        agent_id: str,
        events: EventPublisher,
        policy: SafetyPolicy,
        artifact_dir: Path,
        capture_text: bool = True,
    ) -> None:
        self.capture_text = capture_text
        self.snapshot_id, self.project_id, self.run_id = snapshot_id, project_id, run_id
        self.role_id, self.role_name, self.agent_id = role_id, role_name, agent_id
        self.events, self.policy, self.artifact_dir = events, policy, artifact_dir
        self.stats = Stats()

    # --- states ---------------------------------------------------------
    def _record_state_sync(
        self,
        snap: Snapshot,
        *,
        kind: str,
        parent_state_id: uuid.UUID | None,
        source: str,
        depth: int,
        module: str | None,
        nav_path: list[str],
        title: str | None = None,
    ) -> RecordedState:
        state_id = uuid.uuid4()
        with Session(engine) as s:
            title = (
                title
                or (snap.overlay["name"] if snap.overlay else snap.heading)
                or snap.title
            )
            inserted = s.execute(
                insert(UiState)
                .values(
                    id=state_id,
                    snapshot_id=self.snapshot_id,
                    fingerprint=snap.fingerprint,
                    kind=kind,
                    parent_state_id=parent_state_id,
                    route_template=snap.route,
                    sample_url=snap.url,
                    title=mask_pii(title)[:500],
                    heading=(mask_pii(snap.heading) or None),
                    module=module,
                    nav_path=nav_path,
                    content=snap.content if self.capture_text else {},
                    discovered_by_agent=self.agent_id,
                    discovery_source=source,
                    depth=depth,
                )
                .on_conflict_do_nothing(index_elements=["snapshot_id", "fingerprint"])
                .returning(col(UiState.id))
            ).first()
            is_new = inserted is not None
            if not is_new:
                existing = s.exec(
                    select(UiState.id).where(
                        UiState.snapshot_id == self.snapshot_id,
                        UiState.fingerprint == snap.fingerprint,
                    )
                ).one()
                state_id = existing
            # the role can see this state either way
            s.execute(
                insert(RoleStateAccess)
                .values(
                    snapshot_id=self.snapshot_id,
                    role_id=self.role_id,
                    state_id=state_id,
                    access="visible",
                )
                .on_conflict_do_nothing()
            )
            rec = RecordedState(state_id, is_new, snap.fingerprint)
            if is_new:
                self._record_children(s, snap, state_id, rec)
            s.commit()
        return rec

    def _record_access_sync(
        self, state_id: uuid.UUID, access: str, evidence: str
    ) -> None:
        stmt = insert(RoleStateAccess).values(
            snapshot_id=self.snapshot_id,
            role_id=self.role_id,
            state_id=state_id,
            access=access,
            evidence=evidence[:500],
        )
        with Session(engine) as s:
            s.execute(
                stmt.on_conflict_do_update(
                    index_elements=["snapshot_id", "role_id", "state_id"],
                    set_={"access": access, "evidence": evidence[:500]},
                )
            )
            s.commit()

    async def record_access(
        self, state_id: uuid.UUID, access: str, evidence: str
    ) -> None:
        """What this role got when it opened a screen directly: visible | forbidden | not_found | redirected."""
        await asyncio.to_thread(self._record_access_sync, state_id, access, evidence)

    def _record_children(
        self, s: Session, snap: Snapshot, state_id: uuid.UUID, rec: RecordedState
    ) -> None:
        element_ids: dict[str, uuid.UUID] = {}
        for e in snap.elements:
            label, _ = clean_label(e.label)
            verdict = self.policy.classify(label, e.href)
            row = UiElement(
                state_id=state_id,
                key=e.key[:600],
                kind=e.kind,
                role_aria=e.role,
                label=label,
                name_attr=e.name_attr,
                region=e.region,
                in_row=e.in_row,
                locators=_locators(e),
                attrs={
                    k: v
                    for k, v in {
                        "disabled": e.disabled,
                        "href": e.href,
                        "input_type": e.input_type,
                        "expanded": e.expanded,
                    }.items()
                    if v not in (None, False, "")
                },
                is_submit=e.is_submit,
                is_destructive=verdict.blocked,
                destructive_reason=verdict.reason,
            )
            s.add(row)
            element_ids[e.h] = row.id
        s.flush()
        for f in snap.forms:
            fields = [e for e in snap.elements if e.h in f.fields]
            if not fields:
                continue
            submit = snap.by_handle(f.submit) if f.submit else None
            form = UiForm(
                state_id=state_id,
                name=f.name[:500],
                submit_label=(clean_label(submit.label)[0] if submit else None),
                submit_element_id=element_ids.get(f.submit) if f.submit else None,
            )
            s.add(form)
            s.flush()
            for i, e in enumerate(fields):
                label, star = clean_label(e.label)
                ff = FormField(
                    form_id=form.id,
                    element_id=element_ids.get(e.h),
                    ordinal=i,
                    label=label,
                    input_type=e.input_type,
                    required=e.required or star,
                    min_length=e.minlength,
                    max_length=e.maxlength,
                    min_value=e.min,
                    max_value=e.max,
                    pattern=e.pattern,
                    step=e.step,
                    readonly=e.readonly,
                    placeholder=e.placeholder,
                    options=(
                        {"values": e.options, "truncated": False} if e.options else None
                    ),
                    default_present=e.default_present,
                )
                s.add(ff)
                rec.field_ids[e.h] = ff.id
                rec.field_ids_by_key[e.key[:600]] = ff.id
                self.stats.fields += 1
            self.stats.forms += 1
        self.stats.states += 1

    async def record_state(
        self,
        snap: Snapshot,
        *,
        kind: str,
        parent_state_id: uuid.UUID | None,
        source: str,
        depth: int,
        module: str | None,
        nav_path: list[str],
        title: str | None = None,
    ) -> RecordedState:
        rec = await asyncio.to_thread(
            self._record_state_sync,
            snap,
            kind=kind,
            parent_state_id=parent_state_id,
            source=source,
            depth=depth,
            module=module,
            nav_path=nav_path,
            title=title,
        )
        if rec.is_new:
            await self.events.emit(
                "state_discovered",
                agent_id=self.agent_id,
                state_id=str(rec.state_id),
                kind=kind,
                title=title
                or (snap.overlay["name"] if snap.overlay else snap.heading)
                or snap.title,
                route=snap.route,
                module=module,
                role=self.role_name,
                depth=depth,
                elements=len(snap.elements),
                forms=sum(1 for f in snap.forms if f.fields),
            )
            for f in snap.forms:
                if f.fields:
                    await self.events.emit(
                        "form_recorded",
                        agent_id=self.agent_id,
                        state_id=str(rec.state_id),
                        name=f.name,
                        fields=[
                            clean_label(e.label)[0]
                            for e in snap.elements
                            if e.h in f.fields
                        ],
                    )
        return rec

    def _set_screenshot_sync(self, state_id: uuid.UUID, rel: str) -> None:
        with Session(engine) as s:
            st = s.get(UiState, state_id)
            if st:
                st.screenshot_path = rel
                s.add(st)
                s.commit()

    def screenshot_path(self, state_id: uuid.UUID) -> tuple[Path, str]:
        rel = f"{self.project_id}/runs/{self.run_id}/screenshots/{state_id}.png"
        return self.artifact_dir / rel, rel

    async def set_screenshot(self, state_id: uuid.UUID, rel: str) -> None:
        await asyncio.to_thread(self._set_screenshot_sync, state_id, rel)
        await self.events.emit(
            "screenshot", agent_id=self.agent_id, state_id=str(state_id), path=rel
        )

    # --- probe results / options ---------------------------------------
    def _add_validation_sync(
        self, field_id: uuid.UUID, trigger: str, message: str, source: str
    ) -> None:
        with Session(engine) as s:
            ff = s.get(FormField, field_id)
            if ff is None:
                return
            msgs = list(ff.validation_messages)
            if not any(
                m["trigger"] == trigger and m["message"] == message for m in msgs
            ):
                msgs.append({"trigger": trigger, "message": message, "source": source})
                ff.validation_messages = msgs
                s.add(ff)
                s.commit()

    async def add_validation(
        self, field_id: uuid.UUID, trigger: str, message: str, source: str, label: str
    ) -> None:
        await asyncio.to_thread(
            self._add_validation_sync, field_id, trigger, message, source
        )
        await self.events.emit(
            "metric",
            agent_id=self.agent_id,
            kind="validation_message",
            field=label,
            trigger=trigger,
            message=message,
        )

    def _set_options_sync(
        self, field_id: uuid.UUID, values: list[str], truncated: bool
    ) -> None:
        with Session(engine) as s:
            ff = s.get(FormField, field_id)
            if ff:
                ff.options = {"values": values, "truncated": truncated}
                s.add(ff)
                s.commit()

    async def set_options(
        self, field_id: uuid.UUID, values: list[str], truncated: bool
    ) -> None:
        await asyncio.to_thread(self._set_options_sync, field_id, values, truncated)

    # --- transitions & log ----------------------------------------------
    def _transition_sync(
        self,
        from_id: uuid.UUID,
        to_id: uuid.UUID,
        via_label: str,
        via_key: str | None,
        action: str,
        kind: str,
    ) -> bool:
        with Session(engine) as s:
            exists = s.exec(
                select(Transition.id).where(
                    Transition.snapshot_id == self.snapshot_id,
                    Transition.from_state_id == from_id,
                    Transition.to_state_id == to_id,
                    Transition.via_label == via_label,
                )
            ).first()
            if exists:
                return False
            s.add(
                Transition(
                    snapshot_id=self.snapshot_id,
                    from_state_id=from_id,
                    to_state_id=to_id,
                    via_label=via_label[:500],
                    via_key=via_key,
                    action=action,
                    kind=kind,
                    observed_by_role_id=self.role_id,
                )
            )
            s.commit()
            return True

    async def record_transition(
        self,
        from_id: uuid.UUID,
        to_id: uuid.UUID,
        via_label: str,
        via_key: str | None,
        *,
        action: str = "click",
        kind: str = "navigation",
    ) -> None:
        if from_id == to_id:
            return
        if await asyncio.to_thread(
            self._transition_sync, from_id, to_id, via_label, via_key, action, kind
        ):
            self.stats.transitions += 1
            await self.events.emit(
                "transition_recorded",
                agent_id=self.agent_id,
                from_state=str(from_id),
                to_state=str(to_id),
                via=via_label,
                kind=kind,
            )

    def _log_sync(self, **fields: Any) -> None:
        # reason/source are short codes (varchar 60/20); a long error message must not lose the log row
        if fields.get("reason"):
            fields["reason"] = str(fields["reason"])[:60]
        if fields.get("source"):
            fields["source"] = str(fields["source"])[:20]
        with Session(engine) as s:
            s.add(
                CrawlEvent(
                    run_id=self.run_id,
                    agent_id=self.agent_id,
                    role_id=self.role_id,
                    **fields,
                )
            )
            s.commit()

    async def log(
        self,
        status: str,
        reason: str | None,
        *,
        url: str | None = None,
        action: str | None = None,
        label: str | None = None,
        source: str | None = None,
        state_id: uuid.UUID | None = None,
    ) -> None:
        """status: crawled | skipped | blocked | error"""
        await asyncio.to_thread(
            self._log_sync,
            status=status,
            reason=reason,
            url=url,
            action=action,
            element_label=(label or None) and label[:500],
            source=source,
            state_id=state_id,
        )
        if status == "skipped":
            self.stats.skipped += 1
            await self.events.emit(
                "skipped", agent_id=self.agent_id, reason=reason, label=label, url=url
            )
        elif status == "blocked":
            self.stats.blocked += 1  # the gate emits the live `blocked` event itself


def state_summary_sync(snapshot_id: uuid.UUID) -> dict[str, int]:
    with Session(engine) as s:
        states = s.exec(select(UiState).where(UiState.snapshot_id == snapshot_id)).all()
        state_ids = [st.id for st in states]
        forms = (
            s.exec(select(UiForm).where(col(UiForm.state_id).in_(state_ids))).all()
            if state_ids
            else []
        )
        form_ids = [f.id for f in forms]
        fields = (
            s.exec(select(FormField).where(col(FormField.form_id).in_(form_ids))).all()
            if form_ids
            else []
        )
        trans = s.exec(
            select(Transition).where(Transition.snapshot_id == snapshot_id)
        ).all()
        return {
            "states": len(states),
            "forms": len(forms),
            "fields": len(fields),
            "transitions": len(trans),
        }
