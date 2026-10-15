"""Browser tools for explorer agents (and the deterministic explorer). Every call goes through the ToolGate.

Observation auto-records into the knowledge model: facts come from the browser, not from agent claims."""

from typing import Any
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, Field

from app.qa.browser.extract import Snapshot, clean_label
from app.qa.browser.probe import probe_field
from app.qa.browser.session import ActionError
from app.qa.crawler.session import CrawlSession, Observation, PendingVia
from app.qa.gate.gate import AgentContext, ToolBlocked, ToolDef

MAX_ELEMENTS = 80


class NoArgs(BaseModel):
    pass


class HandleArgs(BaseModel):
    handle: str = Field(
        description="Element handle from the latest snapshot, e.g. 'e12'"
    )


class FillArgs(BaseModel):
    handle: str
    value: str = Field(max_length=500)


class NavigateArgs(BaseModel):
    route: str = Field(description="Path or same-origin URL, e.g. '/items'")
    fresh: bool = Field(
        default=False,
        description="Force a full reload (resets open drawers, tabs, filters)",
    )


class SkipArgs(BaseModel):
    reason: str = Field(max_length=200)
    label: str | None = Field(default=None, max_length=200)


def crawl_of(ctx: AgentContext) -> CrawlSession:
    crawl: CrawlSession = ctx.resources["crawl"]
    return crawl


def compact(
    snap: Snapshot, crawl: CrawlSession, obs: Observation | None
) -> dict[str, Any]:
    """Token-efficient view for an agent: shell chrome once, repeated rows once, capped."""
    seen = crawl.rb.seen_chrome
    first_row: int | None = None
    out: list[dict[str, Any]] = []
    omitted = {"chrome": 0, "repeated_rows": 0, "over_cap": 0}
    for e in snap.elements:
        if e.region in {"nav", "header", "footer"}:
            if e.key in seen:
                omitted["chrome"] += 1
                continue
            seen.add(e.key)
        if e.in_row:
            first_row = e.row_index if first_row is None else first_row
            if e.row_index != first_row:
                omitted["repeated_rows"] += 1
                continue
        if len(out) >= MAX_ELEMENTS:
            omitted["over_cap"] += 1
            continue
        label, star = clean_label(e.label)
        item: dict[str, Any] = {"h": e.h, "kind": e.kind, "label": label}
        if e.region != "main":
            item["region"] = e.region
        if e.href:
            item["href"] = e.href
        if e.kind in {"input", "textarea", "select"}:
            item["type"] = e.input_type
        if e.required or star:
            item["required"] = True
        if e.maxlength:
            item["maxlength"] = e.maxlength
        if e.disabled:
            item["disabled"] = True
        out.append(item)
    return {
        "url": snap.url,
        "heading": snap.heading,
        "overlay": snap.overlay,
        "state_id": str(obs.state_id) if obs else None,
        "new_state": obs.is_new if obs else None,
        "elements": out,
        "omitted": {k: v for k, v in omitted.items() if v},
    }


async def _observe(ctx: AgentContext, source: str) -> tuple[Snapshot, Observation]:
    crawl = crawl_of(ctx)
    snap = await crawl.rb.snapshot()
    obs = await crawl.observe(snap, source=source)
    return snap, obs


async def browser_snapshot(ctx: AgentContext, _args: NoArgs) -> dict[str, Any]:
    crawl = crawl_of(ctx)
    snap, obs = await _observe(ctx, "observe")
    return compact(snap, crawl, obs)


async def browser_click(ctx: AgentContext, args: HandleArgs) -> dict[str, Any]:
    crawl = crawl_of(ctx)
    rb, rec = crawl.rb, crawl.rec
    snap = rb.current
    el = snap.by_handle(args.handle) if snap else None
    if snap is None or el is None:
        raise ActionError(
            f"unknown handle '{args.handle}': take a fresh snapshot first"
        )
    label, _ = clean_label(el.label)

    verdict = crawl.policy.classify(label, el.href)
    if verdict.blocked:
        await rec.log(
            "blocked",
            verdict.reason,
            url=snap.url,
            action="click",
            label=label,
            state_id=crawl.current_state_id,
        )
        raise ToolBlocked(verdict.reason or "denylist")
    if el.is_submit:
        await rec.log(
            "skipped",
            "submit_skipped",
            url=snap.url,
            action="click",
            label=label,
            state_id=crawl.current_state_id,
        )
        raise ToolBlocked("submit_skipped: safe mode never submits forms")
    if el.disabled:
        raise ActionError(f"'{label}' is disabled")
    if el.href and not crawl.is_internal(el.href):
        await rec.log(
            "skipped", "external_link", url=el.href, action="click", label=label
        )
        raise ToolBlocked("external_link")

    before_url, before_fp = snap.url, snap.fingerprint
    if crawl.current_state_id is not None:
        crawl.pending_via = PendingVia(
            crawl.current_state_id, el.key, label, el.kind, el.region, el.in_row
        )
    await rb.click(args.handle)

    # Left the application (or opened a new tab): undo and report.
    if not crawl.is_internal(rb.page.url):
        await rec.log(
            "skipped",
            "external_navigation",
            url=rb.page.url,
            action="click",
            label=label,
        )
        await rb.back()
        crawl.pending_via = None
        snap2, obs2 = await _observe(ctx, "return")
        return {"effect": "external_navigation_reverted", **compact(snap2, crawl, obs2)}

    snap2, obs2 = await _observe(ctx, "click")
    if snap2.overlay:
        effect = "overlay_opened"
    elif snap2.url != before_url:
        effect = "navigated"
    elif snap2.fingerprint != before_fp:
        effect = "state_changed"
    else:
        effect = "no_change"
    await rec.log(
        "crawled",
        None,
        url=snap2.url,
        action="click",
        label=label,
        source="click",
        state_id=obs2.state_id,
    )
    return {"effect": effect, **compact(snap2, crawl, obs2)}


async def browser_fill(ctx: AgentContext, args: FillArgs) -> dict[str, Any]:
    crawl = crawl_of(ctx)
    snap = crawl.rb.current
    el = snap.by_handle(args.handle) if snap else None
    if el is None:
        raise ActionError(f"unknown handle '{args.handle}'")
    if el.kind not in {"input", "textarea"}:
        raise ActionError(f"'{el.label}' is not a text field")
    await crawl.rb.fill(args.handle, args.value)
    return {"filled": clean_label(el.label)[0]}


async def browser_navigate(ctx: AgentContext, args: NavigateArgs) -> dict[str, Any]:
    crawl = crawl_of(ctx)
    url = urljoin(crawl.target_url, args.route)
    if not crawl.is_internal(url):
        await crawl.rec.log("skipped", "external_url", url=url, action="navigate")
        raise ToolBlocked("external_url")
    ok, why = crawl.path_allowed(url)
    if not ok:
        await crawl.rec.log("skipped", why, url=url, action="navigate")
        raise ToolBlocked(why or "excluded")
    if args.fresh:
        crawl.pending_via = (
            None  # a reset reload is housekeeping, not a path through the app
        )
    elif crawl.pending_via is None and crawl.current_state_id is not None:
        crawl.pending_via = PendingVia(
            crawl.current_state_id,
            "",
            f"navigate {urlsplit(url).path or args.route}",
            "navigate",
        )
    await crawl.rb.goto(url, fresh=args.fresh)
    snap, obs = await _observe(ctx, "navigate")
    await crawl.rec.log(
        "crawled",
        None,
        url=snap.url,
        action="navigate",
        source="navigate",
        state_id=obs.state_id,
    )
    return compact(snap, crawl, obs)


async def browser_back(ctx: AgentContext, _args: NoArgs) -> dict[str, Any]:
    crawl = crawl_of(ctx)
    crawl.pending_via = None
    await crawl.rb.back()
    snap, obs = await _observe(ctx, "back")
    return compact(snap, crawl, obs)


async def probe_validation(ctx: AgentContext, _args: NoArgs) -> dict[str, Any]:
    """Trigger the page's own client-side validation on every text field of the visible form(s). Never submits."""
    crawl = crawl_of(ctx)
    snap = crawl.rb.current
    if snap is None:
        raise ActionError("take a snapshot first")
    found: list[dict[str, str]] = []
    probed = 0
    for form in snap.forms:
        for handle in form.fields:
            el = snap.by_handle(handle)
            if el is None:
                continue
            results = await probe_field(crawl.rb, el)
            probed += 1
            label = clean_label(el.label)[0]
            field_id = crawl.field_ids.get(handle)
            for r in results:
                found.append(
                    {"field": label, "trigger": r.trigger, "message": r.message}
                )
                if field_id is not None:
                    await crawl.rec.add_validation(
                        field_id, r.trigger, r.message, r.source, label
                    )
    return {"fields_probed": probed, "messages": found}


async def mark_skipped(ctx: AgentContext, args: SkipArgs) -> dict[str, Any]:
    crawl = crawl_of(ctx)
    await crawl.rec.log(
        "skipped",
        args.reason,
        label=args.label,
        state_id=crawl.current_state_id,
        url=crawl.rb.page.url,
    )
    return {"ok": True}


BROWSER_TOOLS = [
    ToolDef(
        "browser_snapshot",
        "Observe the current screen: heading, open overlay, and interactive elements with handles. Also records it.",
        NoArgs,
        browser_snapshot,
    ),
    ToolDef(
        "browser_click",
        "Click an element by handle; returns the resulting screen. Destructive actions and form submits are blocked in safe mode.",
        HandleArgs,
        browser_click,
    ),
    ToolDef(
        "browser_fill",
        "Type text into a field by handle (does not submit).",
        FillArgs,
        browser_fill,
    ),
    ToolDef(
        "browser_navigate",
        "Open a same-origin path such as '/items'.",
        NavigateArgs,
        browser_navigate,
    ),
    ToolDef("browser_back", "Go back one history entry.", NoArgs, browser_back),
    ToolDef(
        "probe_validation",
        "Trigger client-side validation on the visible form fields to learn required/format/length rules and error messages. Never submits.",
        NoArgs,
        probe_validation,
    ),
    ToolDef(
        "mark_skipped",
        "Record that you deliberately skipped something, with a reason.",
        SkipArgs,
        mark_skipped,
    ),
]
