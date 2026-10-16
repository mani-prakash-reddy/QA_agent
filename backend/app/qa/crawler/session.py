"""CrawlSession: state shared by one role's explorer (deterministic or agentic) and the browser tools."""

import re
import uuid
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from app.qa.browser.extract import (
    Snapshot,
    compute_fingerprint,
    mask_pii,
    route_path,
    route_title,
)
from app.qa.browser.session import RoleBrowser
from app.qa.config import CrawlConfig
from app.qa.gate.safety import SafetyPolicy
from app.qa.knowledge.recorder import KnowledgeRecorder

# Links that are controls, not sections of the application ("Back", "Close"): never a module name.
GENERIC_NAV_LABELS = {
    "back",
    "close",
    "next",
    "previous",
    "cancel",
    "home",
    "menu",
    "more",
    "skip",
    "open",
    "dismiss",
}


def is_section_label(label: str) -> bool:
    """True when a navigation link's label can name a module/section."""
    words = label.strip().lower()
    return (
        bool(words)
        and words not in GENERIC_NAV_LABELS
        and not words.startswith(("back to ", "go back"))
    )


@dataclass
class PendingVia:
    from_state_id: uuid.UUID
    key: str
    label: str
    kind: str  # element kind that was clicked
    region: str = "main"  # nav/header/footer = app shell
    in_row: bool = False
    fills: list[dict[str, str]] = field(
        default_factory=list
    )  # typed before the click (wizard step)


@dataclass
class Observation:
    state_id: uuid.UUID
    is_new: bool
    kind: str
    fingerprint: str


@dataclass
class CrawlSession:
    rb: RoleBrowser
    rec: KnowledgeRecorder
    policy: SafetyPolicy
    cfg: CrawlConfig
    target_url: str
    role_name: str
    origin: str = ""
    nav_labels: dict[str, str] = field(default_factory=dict)  # route path -> menu label
    module_by_state: dict[uuid.UUID, str | None] = field(default_factory=dict)
    depth_by_state: dict[uuid.UUID, int] = field(default_factory=dict)
    field_ids: dict[str, uuid.UUID] = field(
        default_factory=dict
    )  # live handle -> field id (new states only)
    fields_by_state: dict[uuid.UUID, dict[str, uuid.UUID]] = field(
        default_factory=dict
    )  # state -> element key -> field id
    last_obs: Observation | None = None
    current_state_id: uuid.UUID | None = None
    page_state_id: uuid.UUID | None = None
    pending_via: PendingVia | None = None
    pending_fills: list[dict[str, str]] = field(default_factory=list)
    depth_hint: int = 0

    def __post_init__(self) -> None:
        self.origin = urlsplit(self.target_url).netloc

    # --- url policy ---------------------------------------------------
    def is_internal(self, url: str) -> bool:
        parts = urlsplit(url)
        return not parts.netloc or parts.netloc == self.origin

    def path_allowed(self, url: str) -> tuple[bool, str | None]:
        """Include/exclude globs from the project config (glob over the URL path)."""
        path = route_path(url)
        for pat in self.cfg.exclude_globs:
            if _glob(pat, path):
                return False, "excluded_by_config"
        if self.cfg.include_globs and not any(
            _glob(p, path) for p in self.cfg.include_globs
        ):
            return False, "not_in_include_globs"
        return True, None

    # --- observation --------------------------------------------------
    def _learn_nav(self, snap: Snapshot) -> None:
        for e in snap.elements:
            if (
                e.region == "nav"
                and e.kind == "link"
                and is_section_label(
                    e.label
                )  # not icon-only logos or controls like "Back"
                and e.href
                and self.is_internal(e.href)
            ):
                path = route_path(urljoin(snap.url, e.href))
                self.nav_labels.setdefault(path, e.label)

    def _module_for(self, snap: Snapshot, parent: uuid.UUID | None) -> str | None:
        if snap.overlay and parent is not None:
            return self.module_by_state.get(parent)
        path = route_path(snap.url)
        if path in self.nav_labels:
            return self.nav_labels[path]
        first = path.strip("/").split("/")[0]
        return first.replace("-", " ").title() if first else (snap.heading or None)

    async def observe(self, snap: Snapshot, *, source: str) -> Observation:
        self._learn_nav(snap)
        via = self.pending_via
        if snap.overlay:
            # Name unnamed overlays after their opener: "Account menu", not the document title.
            if not snap.overlay["name"] or _EMAIL.search(snap.overlay["name"]):
                snap.overlay["name"] = _overlay_name(via, snap, self.nav_labels)
            # An overlay opened from the app shell is the same overlay on every page.
            if via and via.region in {"nav", "header", "footer"}:
                snap.fingerprint = compute_fingerprint(snap, include_route=False)
            else:
                snap.fingerprint = compute_fingerprint(snap)
        kind = snap.overlay_kind or ("tab" if via and via.kind == "tab" else "page")
        parent = (
            self.page_state_id if kind in {"modal", "menu", "listbox", "tab"} else None
        )
        module = self._module_for(snap, parent)
        depth = (
            self.depth_by_state.get(parent, self.depth_hint) + 1
            if parent and kind != "tab"
            else self.depth_hint
        )
        # The nav label names a page better than its h1 ("Dashboard" vs "Hi, Ana 👋").
        page_title = mask_pii(
            self.nav_labels.get(route_path(snap.url))
            or snap.heading
            or route_title(snap.url)
            or snap.title
        )
        if snap.overlay:
            title = mask_pii(snap.overlay["name"])
        elif kind == "tab" and via:
            title = f"{page_title} › {via.label}"
        else:
            title = page_title
        nav_path = [
            p for p in (module, snap.heading if snap.overlay else None, title) if p
        ]
        nav_path = list(dict.fromkeys(nav_path))

        rec = await self.rec.record_state(
            snap,
            kind=kind,
            parent_state_id=parent,
            source=source,
            depth=depth,
            module=module,
            nav_path=nav_path,
            title=title or None,
        )
        self.module_by_state.setdefault(rec.state_id, module)
        self.depth_by_state.setdefault(rec.state_id, depth)
        if rec.is_new:
            self.field_ids = dict(rec.field_ids)
            self.fields_by_state[rec.state_id] = dict(rec.field_ids_by_key)
            if self.cfg.screenshots != "off":
                path, rel = self.rec.screenshot_path(rec.state_id)
                try:
                    await self.rb.screenshot(path)
                    await self.rec.set_screenshot(rec.state_id, rel)
                except Exception:
                    pass  # a failed screenshot must never fail the crawl
        else:
            self.field_ids = {}

        if via and via.from_state_id != rec.state_id:
            tkind = (
                "overlay"
                if snap.overlay
                else ("tab" if kind == "tab" else "navigation")
            )
            await self.rec.record_transition(
                via.from_state_id,
                rec.state_id,
                via.label,
                via.key,
                kind=tkind,
                fills=via.fills,
            )
        self.pending_via = None
        self.current_state_id = rec.state_id
        if kind in {"page", "tab"}:
            self.page_state_id = rec.state_id
        self.last_obs = Observation(rec.state_id, rec.is_new, kind, rec.fingerprint)
        return self.last_obs


def _glob(pattern: str, path: str) -> bool:
    regex = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return re.fullmatch(regex, path) is not None


_EMAIL = re.compile(r"\S+@\S+")


def _overlay_name(
    via: PendingVia | None, snap: Snapshot, nav_labels: dict[str, str]
) -> str:
    """Name an unlabeled overlay after its opener, without user data ("Account menu")."""
    kind = snap.overlay_kind or "overlay"
    if via and _EMAIL.search(via.label):
        return f"Account {kind}"  # avatar/user buttons are labelled with the user's name and e-mail
    label = via.label.strip() if via else ""
    if not label:
        where = nav_labels.get(route_path(snap.url)) or snap.heading
        return (
            f"{where} row actions {kind}"
            if via and via.in_row
            else f"{where} {kind}".strip()
        )
    # "Show Privacy and Security Menu" + "menu" would read "...Menu menu"
    return label if label.lower().endswith(kind) else f"{label} {kind}"
