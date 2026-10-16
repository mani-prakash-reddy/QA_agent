"""Deterministic explorer: no LLM. Drives the same gated browser tools an agent uses.

It is the baseline for evaluation and the fallback when a model handles tool use poorly."""

import logging
import re
import uuid
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.qa.agents.budget import BudgetExceeded
from app.qa.browser.extract import Element, Snapshot, clean_label, route_template
from app.qa.crawler.session import CrawlSession, PendingVia
from app.qa.gate.gate import AgentContext, ToolGate

logger = logging.getLogger(__name__)

CLICKABLE = {"button", "tab", "menuitem", "combobox"}
TOGGLES = {"checkbox", "radio", "switch"}
IGNORED_SCHEMES = ("mailto:", "tel:", "javascript:", "data:")


def _ignored_href(href: str) -> bool:
    """A same-page anchor ("#top") is not a screen; a hash route ("#/tickets") is."""
    if href.startswith("#"):
        return not href.startswith(("#/", "#!/"))
    return href.startswith(IGNORED_SCHEMES)


# Addresses that download a file or a feed (exports): not screens, and navigating to them aborts.
DOWNLOAD_RE = re.compile(
    r"\.(csv|pdf|atom|rss|xml|zip|gz|xlsx?|docx?|pptx?|png|jpe?g|gif|svg|ico|txt|diff|patch)(\?|#|$)",
    re.I,
)
MAX_CLICKS_PER_STATE = 40
REPEAT_THRESHOLD = (
    3  # this many look-alike controls in one screen = a list of the same component
)


def _group(e: Element) -> tuple[str, str, str]:
    """Look-alike key: same kind and region, same first words ("Add to Basket", "Click for more
    information about the product <name>")."""
    words = clean_label(e.label)[0].lower().split()[:4]
    return (e.kind, e.region, " ".join(words))


@dataclass
class Frontier:
    url: str
    depth: int
    from_state_id: uuid.UUID | None
    via_label: str
    via_key: str | None


class ReopenFailed(Exception):
    pass


class BfsExplorer:
    def __init__(self, ctx: AgentContext, gate: ToolGate, crawl: CrawlSession) -> None:
        self.ctx, self.gate, self.crawl = ctx, gate, crawl
        self.cfg = crawl.cfg
        self.rb, self.rec = crawl.rb, crawl.rec
        self.queue: deque[Frontier] = deque()
        self.queued: set[str] = set()  # route templates already queued/visited
        self.external_logged: set[str] = set()
        self._shell_re = (
            re.compile(
                "|".join(f"(?:{x})" for x in self.cfg.shell_toggle_patterns), re.I
            )
            if self.cfg.shell_toggle_patterns
            else None
        )
        self._base_fp: str | None = None  # fingerprint of the page being explored
        # Screens THIS role already explored. A screen another role recorded still has to be explored
        # once per role: its hidden drawers and menus can differ.
        self._explored: set[str] = set()

    # --- plumbing ---------------------------------------------------------
    async def call(self, tool: str, **args: Any) -> dict[str, Any]:
        return await self.gate.invoke(self.ctx, tool, args)

    def _states_left(self) -> bool:
        return self.rec.stats.states < self.cfg.max_states

    # --- main loop --------------------------------------------------------
    async def run(self) -> dict[str, Any]:
        start = self.rb.page.url
        self._enqueue(start, 0, None, "start", None)
        for seed in self.cfg.seeds:
            self._enqueue(urljoin(self.crawl.target_url, seed), 0, None, "seed", None)
        try:
            while self.queue and self._states_left():
                item = self.queue.popleft()
                await self._explore_page(item)
        except BudgetExceeded:
            await self.rec.log(
                "skipped",
                "budget",
                label=f"{len(self.queue)} pages left in the frontier",
            )
            raise
        if self.queue:
            await self.rec.log(
                "skipped",
                "max_states",
                label=f"{len(self.queue)} pages left in frontier",
            )
        return self.rec.stats.as_dict()

    def _enqueue(
        self,
        url: str,
        depth: int,
        from_state: uuid.UUID | None,
        label: str,
        key: str | None,
    ) -> bool:
        route = route_template(url)
        if route in self.queued:
            return False
        self.queued.add(route)
        self.queue.append(Frontier(url, depth, from_state, label, key))
        return True

    async def _explore_page(self, item: Frontier) -> None:
        if item.depth > self.cfg.max_depth:
            await self.rec.log("skipped", "depth", url=item.url)
            return
        ok, why = self.crawl.path_allowed(item.url)
        if not ok:
            await self.rec.log("skipped", why, url=item.url)
            return
        self.crawl.depth_hint = item.depth
        if item.from_state_id is not None:
            self.crawl.pending_via = PendingVia(
                item.from_state_id, item.via_key or "", item.via_label, "link"
            )
        res = await self.call("browser_navigate", route=item.url)
        if res.get("error") or res.get("blocked"):
            await self.rec.log(
                "error",
                str(res.get("error") or res.get("reason")),
                url=item.url,
                action="navigate",
            )
            return
        obs = self.crawl.last_obs
        snap = self.rb.current
        if snap is None or obs is None:
            return
        # A redirect can land on a route we already handled (e.g. unauthenticated area -> dashboard).
        self.queued.add(snap.route)
        if obs.fingerprint in self._explored:
            self._discover_links(snap, item.depth, obs.state_id)
            await self._flush_pending_logs()
            await self.rec.log(
                "skipped", "duplicate_state", url=snap.url, source="link"
            )
            return
        self._explored.add(obs.fingerprint)
        self._base_fp = obs.fingerprint
        self._discover_links(snap, item.depth, obs.state_id)
        await self._probe_forms(snap)
        await self._click_elements(snap, item.depth, item.url)

    # --- links ------------------------------------------------------------
    def _discover_links(
        self, snap: Snapshot, depth: int, from_state: uuid.UUID
    ) -> None:
        for e in snap.elements:
            if not e.href or _ignored_href(e.href):
                continue
            url = urljoin(snap.url, e.href)
            label = clean_label(e.label)[0]
            if DOWNLOAD_RE.search(urlsplit(url).path + ("?" if "?" in url else "")):
                self.ctx.resources.setdefault("_pending_logs", []).append(
                    ("download_link", url, label)
                )
                continue
            verdict = self.crawl.policy.classify(label, e.href)
            if (
                verdict.blocked
            ):  # "Sign out", a delete link: never opened by address either
                self.ctx.resources.setdefault("_pending_logs", []).append(
                    (verdict.reason or "denylist", url, label)
                )
                continue
            if not self.crawl.is_internal(url):
                host = urlsplit(url).netloc
                if (
                    host not in self.external_logged
                ):  # one row per external site, not per link
                    self.external_logged.add(host)
                    self.ctx.resources.setdefault("_pending_logs", []).append(
                        ("external_link", url, label)
                    )
                continue
            self._enqueue(url, depth + 1, from_state, label, e.key)

    async def _flush_pending_logs(self) -> None:
        for reason, url, label in self.ctx.resources.pop("_pending_logs", []):
            await self.rec.log("skipped", reason, url=url, label=label, source="link")

    # --- forms ------------------------------------------------------------
    async def _probe_forms(self, snap: Snapshot) -> None:
        if (
            any(f.fields for f in snap.forms)
            and self.crawl.last_obs
            and self.crawl.last_obs.is_new
        ):
            await self.call("probe_validation")

    # --- clicking ---------------------------------------------------------
    def _eligible(self, el: Element, first_row: int | None) -> tuple[bool, str | None]:
        if el.kind in TOGGLES:
            return False, "toggle_skipped"
        if el.kind not in CLICKABLE:
            return False, None  # inputs/links/options are not click targets
        if el.disabled:
            return False, "disabled"
        if self._shell_re and self._shell_re.search(el.label):
            return False, "shell_toggle"
        if el.in_row and first_row is not None and el.row_index != first_row:
            return False, "repeated_row_action"
        return True, None

    async def _click_elements(self, snap: Snapshot, depth: int, page_url: str) -> None:
        await self._flush_pending_logs()
        first_row = next((e.row_index for e in snap.elements if e.in_row), None)
        clickable = [e for e in snap.elements if e.kind in CLICKABLE]
        group_size = Counter(_group(e) for e in clickable)
        seen_groups: set[tuple[str, str, str]] = set()
        keys: list[tuple[str, str]] = []
        for e in snap.elements:
            ok, why = self._eligible(e, first_row)
            if ok:
                g = _group(e)
                if group_size[g] >= REPEAT_THRESHOLD and g in seen_groups:
                    ok, why = (
                        False,
                        "repeated_component",
                    )  # same card/button repeated: explore one
                seen_groups.add(g)
            if ok:
                keys.append((e.key, clean_label(e.label)[0]))
            elif why:
                await self.rec.log(
                    "skipped",
                    why,
                    url=snap.url,
                    label=clean_label(e.label)[0],
                    source="click",
                )
        for key, _label in keys[:MAX_CLICKS_PER_STATE]:
            if not self._states_left():
                return
            cur = self.rb.current
            el = cur.by_key(key) if cur else None
            if el is None:
                fresh = await self.call("browser_snapshot")
                el = (
                    self.rb.current.by_key(key)
                    if self.rb.current and not fresh.get("error")
                    else None
                )
            if el is None:
                await self.rec.log(
                    "skipped", "element_gone", url=page_url, label=_label
                )
                continue
            await self._click_and_explore(page_url, el, depth, [key])
            await self._restore(page_url)
        if len(keys) > MAX_CLICKS_PER_STATE:
            await self.rec.log(
                "skipped",
                "click_cap",
                url=page_url,
                label=f"{len(keys) - MAX_CLICKS_PER_STATE} elements",
            )

    async def _click_and_explore(
        self, page_url: str, el: Element, depth: int, path: list[str]
    ) -> None:
        opener_state = self.crawl.current_state_id
        opener_key = el.key
        res = await self.call("browser_click", handle=el.h)
        if res.get("blocked") or res.get("error"):
            return  # logged by the gate (denylist, submit, external, element gone)
        effect = res.get("effect")
        snap = self.rb.current
        if snap is None:
            return
        if effect == "overlay_opened":
            await self._explore_overlay(page_url, depth, path, opener_state, opener_key)
        elif effect == "navigated":
            self._discover_links(
                snap, depth, self.crawl.current_state_id or opener_state or uuid.uuid4()
            )
            if self.crawl.is_internal(snap.url):
                self._enqueue(
                    snap.url, depth + 1, opener_state, clean_label(el.label)[0], el.key
                )
        elif (
            effect == "state_changed"
            and self.crawl.last_obs
            and self.crawl.last_obs.is_new
        ):
            # e.g. a tab: record its links and forms too
            self._discover_links(snap, depth, self.crawl.last_obs.state_id)
            await self._probe_forms(snap)

    async def _explore_overlay(
        self,
        page_url: str,
        depth: int,
        path: list[str],
        opener_state: uuid.UUID | None,
        opener_key: str,
    ) -> None:
        obs = self.crawl.last_obs
        snap = self.rb.current
        if obs is None or snap is None:
            return
        kind = obs.kind
        if kind == "listbox":
            values = [
                clean_label(e.label)[0] for e in snap.elements if e.kind == "option"
            ]
            fid = (
                self.crawl.fields_by_state.get(opener_state, {}).get(opener_key)
                if opener_state
                else None
            )
            if fid and values:
                await self.rec.set_options(fid, values[:50], truncated=len(values) > 50)
        elif obs.is_new and kind == "modal":
            await self._probe_forms(snap)
            await self._explore_comboboxes(page_url, depth, path, obs.state_id)
        elif kind == "menu" and obs.fingerprint not in self._explored:
            self._explored.add(obs.fingerprint)
            await self._explore_menu(page_url, depth, path)
            return  # menu exploration closes/restores itself
        await self._close_overlay(page_url)

    async def _explore_comboboxes(
        self, page_url: str, depth: int, path: list[str], modal_state: uuid.UUID
    ) -> None:
        snap = self.rb.current
        if snap is None:
            return
        keys = [e.key for e in snap.elements if e.kind == "combobox" and not e.disabled]
        for key in keys:
            cur = self.rb.current
            el = cur.by_key(key) if cur else None
            if el is None:
                continue
            res = await self.call("browser_click", handle=el.h)
            if res.get("error") or res.get("blocked"):
                continue
            opener_snap = self.rb.current
            if opener_snap and opener_snap.overlay_kind == "listbox":
                values = [
                    clean_label(e.label)[0]
                    for e in opener_snap.elements
                    if e.kind == "option"
                ]
                fid = self.crawl.fields_by_state.get(modal_state, {}).get(key)
                if fid and values:
                    await self.rec.set_options(
                        fid, values[:50], truncated=len(values) > 50
                    )
            await self.rb.press("Escape")
            await self.call("browser_snapshot")

    async def _explore_menu(self, page_url: str, depth: int, path: list[str]) -> None:
        snap = self.rb.current
        if snap is None:
            return
        items = [e for e in snap.elements if e.kind == "menuitem"]
        keys = [(e.key, e.href, clean_label(e.label)[0]) for e in items]
        for i, (key, href, label) in enumerate(keys):
            if not self._states_left():
                break
            if i > 0:
                try:
                    await self._reopen(page_url, path)
                except ReopenFailed:
                    await self.rec.log(
                        "skipped", "reopen_failed", url=page_url, label=label
                    )
                    break
            cur = self.rb.current
            el = cur.by_key(key) if cur else None
            if el is None:
                continue
            if (
                href
                and not _ignored_href(href)
                and self.crawl.is_internal(urljoin(page_url, href))
            ):
                # a link in a menu: queue it instead of clicking
                self._enqueue(
                    urljoin(page_url, href),
                    depth + 1,
                    self.crawl.current_state_id,
                    label,
                    key,
                )
                await self._close_overlay(page_url)
                continue
            await self._click_and_explore(page_url, el, depth + 1, [*path, key])
        await self._restore(page_url)

    # --- navigation helpers ---------------------------------------------------
    async def _close_overlay(self, page_url: str) -> None:
        await self.rb.press("Escape")
        snap = (await self.call("browser_snapshot")) and self.rb.current
        if snap and snap.overlay:
            closer = next(
                (
                    e
                    for e in snap.elements
                    if e.kind == "button"
                    and clean_label(e.label)[0].lower()
                    in {"close", "cancel", "dismiss"}
                ),
                None,
            )
            if closer:
                self.crawl.pending_via = None
                await self.rb.click(closer.h)
                await self.call("browser_snapshot")
        if self.rb.current and self.rb.current.overlay:
            await self.call("browser_navigate", route=page_url, fresh=True)

    async def _restore(self, page_url: str) -> None:
        """Back to the base state of the page (no overlay, same tab/content) for the next element."""
        snap = self.rb.current
        if snap and snap.overlay:
            await self._close_overlay(page_url)
            snap = self.rb.current
        if (
            snap is None
            or route_template(snap.url) != route_template(page_url)
            or (self._base_fp is not None and snap.fingerprint != self._base_fp)
        ):
            # A reload is the only reliable way to reset an SPA (tabs, filters, expanded panels).
            self.crawl.pending_via = None
            await self.call("browser_navigate", route=page_url, fresh=True)

    async def _reopen(self, page_url: str, path: list[str]) -> None:
        self.crawl.pending_via = None
        await self.call("browser_navigate", route=page_url, fresh=True)
        for key in path:
            cur = self.rb.current
            el = cur.by_key(key) if cur else None
            if el is None:
                raise ReopenFailed(key)
            res = await self.call("browser_click", handle=el.h)
            if res.get("error") or res.get("blocked"):
                raise ReopenFailed(key)
