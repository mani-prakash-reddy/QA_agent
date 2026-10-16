"""Click paths from the application's start page to any recorded screen, planned over the recorded
transitions (breadth first). The crawler's own `nav_path` is only the first path it happened to take
and can omit the menu that had to be opened; the transition graph cannot."""

import re
import uuid
from collections import deque
from dataclasses import dataclass, field

from sqlmodel import Session, col, select

from app.qa.models import Transition, UiElement, UiState

Step = tuple[str, str]  # (label, element kind)

# A label that is somebody's data (e-mail, URL, date, id): a control named after it is not reproducible.
DATA_LIKE = re.compile(r"@|https?://|\d{3,}|\d+[/.-]\d+[/.-]\d+")


def direct_route(state: UiState) -> str | None:
    """A route that can be opened directly (`/profile`, `#/basket`), or None if it is parameterised."""
    r = state.route_template or ""
    if state.kind != "page" or not r or r == "blank" or any(c in r for c in ":<{*"):
        return None
    return r if r.startswith(("/", "#/")) else None


@dataclass
class PathPlanner:
    start: UiState | None
    direct: dict[uuid.UUID, str] = field(
        default_factory=dict
    )  # page -> route that opens it
    adj: dict[uuid.UUID, list[tuple[uuid.UUID, str, str, bool]]] = field(
        default_factory=dict
    )  # from -> [(to, label, kind, in_row)]
    edge_roles: dict[tuple[uuid.UUID, uuid.UUID, str], set[uuid.UUID]] = field(
        default_factory=dict
    )  # (from, to, label) -> roles that took that click
    _cache: dict[tuple[uuid.UUID, uuid.UUID | None], tuple[list[Step], bool] | None] = (
        field(default_factory=dict)
    )

    @property
    def start_title(self) -> str:
        # Start pages are often personalised ("Hi, <name>"): never put one in a test case.
        return ""

    def _search(
        self,
        state_id: uuid.UUID,
        seeds: list[tuple[uuid.UUID, list[Step]]],
        role_id: uuid.UUID | None = None,
    ) -> tuple[list[Step], bool] | None:
        seen = {sid for sid, _ in seeds}
        queue: deque[tuple[uuid.UUID, list[Step], bool]] = deque(
            (sid, path, False) for sid, path in seeds
        )
        for sid, path, _ in list(queue):
            if sid == state_id:
                return path, False
        while queue:
            cur, path, rows = queue.popleft()
            for to, label, kind, in_row in self.adj.get(cur, []):
                if to in seen:
                    continue
                if role_id is not None:
                    taken = self.edge_roles.get((cur, to, label))
                    if taken and role_id not in taken:
                        continue  # a click this role never had
                seen.add(to)
                nxt = [*path, (label, kind)]
                if to == state_id:
                    return nxt, rows or in_row
                queue.append((to, nxt, rows or in_row))
        return None

    def plan(
        self, state_id: uuid.UUID, role_id: uuid.UUID | None = None
    ) -> tuple[list[Step], bool] | None:
        """(clicks from the start page, a per-row control is involved), or None if unreachable.

        Clicks from the start page are preferred. Failing that, a page that can be opened by its
        address (kind "url") becomes an extra starting point, so a single-page app whose menu the
        crawler never recorded is still reachable."""
        key = (state_id, role_id)
        if key in self._cache:
            return self._cache[key]
        result: tuple[list[Step], bool] | None = None
        if self.start is not None:
            # With a role: clicks that role actually took are preferred (another role's menu may
            # not exist for it); fall back to any recorded path.
            for rid in (role_id, None) if role_id else (None,):
                result = self._search(state_id, [(self.start.id, [])], rid)
                if result is None and self.direct:
                    seeds: list[tuple[uuid.UUID, list[Step]]] = [(self.start.id, [])]
                    seeds += [
                        (sid, [(route, "url")]) for sid, route in self.direct.items()
                    ]
                    result = self._search(state_id, seeds, rid)
                if result is not None:
                    break
        # A page with a plain address is opened by that address when the clicks to it are long: shorter,
        # and it does not depend on menus or on data rows (a user's profile link) that exist for one role.
        direct = self.direct.get(state_id)
        if direct and (result is None or len(result[0]) > 1):
            result = ([(direct, "url")], False)
        self._cache[key] = result
        return result


def build_planner(session: Session, snapshot_id: uuid.UUID) -> PathPlanner:
    states = list(
        session.exec(select(UiState).where(UiState.snapshot_id == snapshot_id)).all()
    )
    pages = [s for s in states if s.kind == "page"]
    start = min(
        pages or states, key=lambda s: (s.depth, s.route_template or "~"), default=None
    )
    planner = PathPlanner(start)
    planner.direct = {
        s.id: r for s in pages if s is not start and (r := direct_route(s))
    }
    if not states:
        return planner
    elements = {
        (e.state_id, e.label): (e.kind, e.in_row)
        for e in session.exec(
            select(UiElement).where(col(UiElement.state_id).in_([s.id for s in states]))
        )
        if e.label
    }
    for t in session.exec(
        select(Transition).where(Transition.snapshot_id == snapshot_id)
    ):
        if not t.via_label or t.from_state_id == t.to_state_id:
            continue
        kind, in_row = elements.get((t.from_state_id, t.via_label), ("button", False))
        label = t.via_label
        if t.via_label.startswith("navigate ") and (
            (t.via_label[9:]).startswith(("/", "#/"))
        ):
            # the crawler opened this address itself (a seed, a typed route): not a control to press
            kind, in_row, label = "url", False, t.via_label[9:]
        planner.adj.setdefault(t.from_state_id, []).append(
            (t.to_state_id, label, kind, in_row)
        )
        if t.observed_by_role_id:
            planner.edge_roles.setdefault(
                (t.from_state_id, t.to_state_id, label), set()
            ).add(t.observed_by_role_id)
    return planner
