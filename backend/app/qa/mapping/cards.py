"""Screen "cards": the compact text description of a screen used for retrieval and for the judge.

A card holds only what the crawler observed (headings, labels, fields, validation messages): it is
the evidence a mapping may cite. Screens that are variants of one route are grouped so a data-
dependent page (empty vs populated) is one candidate, not several."""

import re
import uuid
from dataclasses import dataclass, field

from sqlmodel import Session, col, select

from app.qa.mapping.paths import DATA_LIKE, build_planner
from app.qa.models import FormField, Transition, UiElement, UiForm, UiState

MAPPABLE_KINDS = {"page", "modal", "tab"}  # menus/listboxes are chrome, not features
MAX_ITEMS = 24
CARD_CHARS = 1900
SHELL_SHARE = 0.4  # a control on >= 40% of screens belongs to the shell


def is_row_data(e: UiElement) -> bool:
    """A control whose label is somebody's data (an e-mail, a URL, a date, an id: a list row, the
    signed-in user's name on the account menu). It exists for that data only, so a test must not name it."""
    return bool(e.label and DATA_LIKE.search(e.label))


def norm_label(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass
class ScreenCard:
    group_key: str
    state_id: uuid.UUID  # representative state (the richest variant)
    state_ids: list[uuid.UUID]
    title: str
    kind: str
    route: str | None
    module: str | None
    nav: list[str]
    text: str
    labels: set[str] = field(default_factory=set)  # normalized, citable
    destructive: set[str] = field(default_factory=set)  # labels a test must never click
    kinds: dict[str, str] = field(
        default_factory=dict
    )  # normalized label -> element kind
    row_labels: set[str] = field(
        default_factory=set
    )  # per-row controls (a row must exist)
    open_path: list[str] = field(default_factory=list)  # labels to press to reach it
    path: list[tuple[str, str]] | None = (
        None  # clicks from the start page; None = unreachable
    )
    needs_rows: bool = False
    element_count: int = 0

    @property
    def display(self) -> str:
        return self.title or self.route or str(self.state_id)


def _uniq(items: list[str], cap: int = MAX_ITEMS) -> list[str]:
    seen: dict[str, None] = {}
    for i in items:
        i = i.strip()
        if i and norm_label(i) not in {norm_label(s) for s in seen}:
            seen[i] = None
        if len(seen) >= cap:
            break
    return list(seen)


def _nav_labels(nav_path: list[object]) -> list[str]:
    out: list[str] = []
    for step in nav_path:
        if isinstance(step, dict):
            label = step.get("label") or step.get("via") or step.get("name")
            if label:
                out.append(str(label))
        elif isinstance(step, str):
            out.append(step)
    return out


def build_card_text(
    state: UiState,
    elements: list[UiElement],
    forms: list[tuple[UiForm, list[FormField]]],
    inbound: list[str],
    chrome: frozenset[str] = frozenset(),
) -> tuple[str, set[str]]:
    labels: set[str] = set()
    lines: list[str] = []
    name = state.heading or state.title or state.route_template or "(untitled)"
    lines.append(f"Screen: {name} [{state.kind}]")
    labels.add(norm_label(name))
    if state.title and state.title != name:
        lines.append(f"Title: {state.title}")
        labels.add(norm_label(state.title))
    if state.route_template:
        lines.append(f"Route: {state.route_template}")
    if state.module:
        lines.append(f"Module: {state.module}")
    nav = _nav_labels(state.nav_path) or inbound
    if nav:
        lines.append("Reached via: " + " > ".join(nav[-4:]))
        labels.update(norm_label(n) for n in nav)
    if state.summary:
        lines.append(f"Purpose: {state.summary}")
    controls = _uniq(
        [
            e.label
            for e in elements
            if e.kind in {"button", "link", "tab", "menuitem", "checkbox", "switch"}
            and e.label
            and not e.is_destructive
        ],
        cap=300,
    )
    shell = [c for c in controls if norm_label(c) in chrome][:12]
    controls = [c for c in controls if norm_label(c) not in chrome][:MAX_ITEMS]
    destructive = _uniq([e.label for e in elements if e.is_destructive and e.label], 8)
    if controls:
        lines.append("Controls: " + "; ".join(controls))
        labels.update(norm_label(c) for c in controls)
    if shell:
        lines.append("Shell controls (shared across the app): " + "; ".join(shell))
        labels.update(norm_label(c) for c in shell)
    if destructive:
        lines.append("Destructive controls (not exercised): " + "; ".join(destructive))
        labels.update(norm_label(c) for c in destructive)
    content = state.content or {}
    for cols in content.get("headers") or []:
        lines.append("Table columns: " + " | ".join(cols))
        labels.update(norm_label(c) for c in cols)
    shown = [t for t in content.get("text") or [] if norm_label(t) not in labels][:14]
    if shown:
        lines.append("Text on screen: " + "; ".join(shown))
        labels.update(norm_label(t) for t in shown)
    for form, fields in forms:
        parts = []
        for f in fields:
            bits = [f.input_type] + (["required"] if f.required else [])
            if f.max_length:
                bits.append(f"max {f.max_length}")
            if f.min_length:
                bits.append(f"min {f.min_length}")
            parts.append(f"{f.label or '(unlabelled)'} ({', '.join(bits)})")
            if f.label:
                labels.add(norm_label(f.label))
            for m in f.validation_messages or []:
                if isinstance(m, dict) and m.get("message"):
                    labels.add(norm_label(str(m["message"])))
        title = form.name or form.submit_label or "form"
        lines.append(
            f"Form '{title}'"
            + (f" (submit: {form.submit_label})" if form.submit_label else "")
            + (": " + "; ".join(parts) if parts else "")
        )
        if form.name:
            labels.add(norm_label(form.name))
        if form.submit_label:
            labels.add(norm_label(form.submit_label))
    text = "\n".join(lines)
    return text[:CARD_CHARS], {x for x in labels if x}


def shell_labels(per_screen: list[list[UiElement]]) -> frozenset[str]:
    """Controls that appear on most screens are the application shell (toolbar, side menu): they say
    nothing about what a screen is for, and would crowd out its distinctive controls."""
    if len(per_screen) < 4:
        return frozenset()
    seen: dict[str, int] = {}
    for els in per_screen:
        for label in {norm_label(e.label) for e in els if e.label}:
            seen[label] = seen.get(label, 0) + 1
    return frozenset(k for k, n in seen.items() if n >= SHELL_SHARE * len(per_screen))


def click_path(nav: list[str], *titles: str | None) -> list[str]:
    """The labels to press to reach a screen. The recorded path often ends with the screen's own
    title ("Settings > User Settings"), which is a heading, not a control."""
    own = {norm_label(t) for t in titles if t}
    out = list(nav)
    while len(out) > 1 and norm_label(out[-1]) in own:
        out.pop()
    return out


def group_key_of(state: UiState) -> str:
    return "|".join(
        [
            state.kind,
            state.route_template or "",
            norm_label(state.heading or state.title),
        ]
    )


def load_cards(
    session: Session, snapshot_id: uuid.UUID, *, hide_row_data: bool = False
) -> list[ScreenCard]:
    """`hide_row_data`: leave out row-data controls (for test authoring only; mapping keeps them as evidence)."""
    planner = build_planner(session, snapshot_id)
    states = list(
        session.exec(select(UiState).where(UiState.snapshot_id == snapshot_id)).all()
    )
    states = [s for s in states if s.kind in MAPPABLE_KINDS]
    if not states:
        return []
    ids = [s.id for s in states]
    elements: dict[uuid.UUID, list[UiElement]] = {}
    for e in session.exec(select(UiElement).where(col(UiElement.state_id).in_(ids))):
        if hide_row_data and is_row_data(e):
            continue
        elements.setdefault(e.state_id, []).append(e)
    forms: dict[uuid.UUID, list[tuple[UiForm, list[FormField]]]] = {}
    form_rows = list(session.exec(select(UiForm).where(col(UiForm.state_id).in_(ids))))
    fields: dict[uuid.UUID, list[FormField]] = {}
    if form_rows:
        for f in session.exec(
            select(FormField)
            .where(col(FormField.form_id).in_([f.id for f in form_rows]))
            .order_by(col(FormField.ordinal))
        ):
            fields.setdefault(f.form_id, []).append(f)
    for form in form_rows:
        forms.setdefault(form.state_id, []).append((form, fields.get(form.id, [])))
    inbound: dict[uuid.UUID, list[str]] = {}
    for t in session.exec(
        select(Transition).where(Transition.snapshot_id == snapshot_id)
    ):
        if t.via_label:
            inbound.setdefault(t.to_state_id, []).append(t.via_label)

    grouped: dict[str, list[UiState]] = {}
    for s in states:
        grouped.setdefault(group_key_of(s), []).append(s)
    reps: list[tuple[str, UiState, list[UiState]]] = []
    for key, members in grouped.items():

        def weight(s: UiState) -> int:
            return len(elements.get(s.id, [])) + 3 * len(forms.get(s.id, []))

        rep = max(members, key=weight)
        if (
            weight(rep) == 0
        ):  # a blank or error shell: nothing a requirement could map to
            continue
        reps.append((key, rep, members))
    chrome = shell_labels([elements.get(rep.id, []) for _, rep, _ in reps])
    cards: list[ScreenCard] = []
    for key, rep, members in reps:
        text, labels = build_card_text(
            rep,
            elements.get(rep.id, []),
            forms.get(rep.id, []),
            inbound.get(rep.id, [])[:3],
            chrome,
        )
        cards.append(
            ScreenCard(
                group_key=key,
                state_id=rep.id,
                state_ids=[m.id for m in members],
                title=rep.heading or rep.title,
                kind=rep.kind,
                route=rep.route_template,
                module=rep.module,
                nav=_nav_labels(rep.nav_path),
                path=(planned[0] if (planned := planner.plan(rep.id)) else None),
                needs_rows=bool(planned and planned[1]),
                open_path=click_path(_nav_labels(rep.nav_path), rep.title, rep.heading),
                text=text,
                labels=labels,
                element_count=len(elements.get(rep.id, [])),
                row_labels={
                    norm_label(e.label)
                    for e in elements.get(rep.id, [])
                    if e.in_row and e.label
                },
                kinds={
                    **{
                        norm_label(e.label): e.kind
                        for e in elements.get(rep.id, [])
                        if e.label
                    },
                    **{
                        norm_label(f.label): "field"
                        for _fm, fs in forms.get(rep.id, [])
                        for f in fs
                        if f.label
                    },
                },
                destructive={
                    norm_label(e.label)
                    for e in elements.get(rep.id, [])
                    if e.is_destructive and e.label
                },
            )
        )
    cards.sort(key=lambda c: (c.route or "", c.title))
    return cards
