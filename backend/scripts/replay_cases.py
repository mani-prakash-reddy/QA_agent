"""Dry-run replay of generated test cases against the live application (an executability check).

    uv run python scripts/replay_cases.py "<project>" [--base-url http://localhost:3000] [--limit 60]

For each case it signs in as the case's role (credentials come from the project, decrypted here and
never printed), performs the navigation, field fills and non-final clicks, and checks that every
control the steps name can be found. It does NOT submit forms (the last click of a form case is only
located, never pressed) and the safe-mode request guard blocks any write that would slip through.
`verify` steps need a person and are reported separately. The output is the share of cases whose
every actionable step resolved: "executable"."""

import argparse
import asyncio
import json
import random
import sys
from collections import Counter
from typing import Any

from playwright.async_api import Locator, Page, async_playwright
from sqlmodel import Session, select

from app.core.db import engine
from app.qa.browser.auth import load_role_credentials
from app.qa.browser.session import RoleBrowser
from app.qa.models import AppRole, Project, TestCase

CLICKABLE = ("link", "button", "tab", "menuitem", "checkbox", "option")
FORM_TYPES = {"positive", "negative", "boundary", "field_validation"}


async def first(page: Page, label: str, roles: tuple[str, ...]) -> Locator | None:
    """Prefer real controls (by role) over any text that happens to match; wait briefly for menus and
    dialogs that animate in."""
    for _ in range(3):
        for exact in (True, False):
            for role in roles:
                loc = page.get_by_role(role, name=label, exact=exact)  # type: ignore[arg-type]
                if await loc.count():
                    return loc.first
        for exact in (True, False):
            text = page.get_by_text(label, exact=exact).locator("visible=true")
            if await text.count():
                return (
                    text.first.locator(
                        "xpath=ancestor-or-self::*[self::a or self::button or @role='button' "
                        "or @role='menuitem' or @role='tab'][1]"
                    )
                    .or_(text.first)
                    .first
                )
        await page.wait_for_timeout(500)
    return None


async def field(page: Page, label: str) -> Locator | None:
    # The crawler stores labels without their "*" required marker, so fall back to a substring match.
    for _ in range(3):
        for exact in (True, False):
            for loc in (
                page.get_by_label(label, exact=exact),
                page.get_by_placeholder(label, exact=exact),
                page.get_by_role("textbox", name=label, exact=exact),
                page.get_by_role("combobox", name=label, exact=exact),
                page.get_by_role("spinbutton", name=label, exact=exact),
            ):
                if await loc.count():
                    return loc.first
        await page.wait_for_timeout(500)
    return None


def text_of(value: dict[str, Any] | None) -> str:
    if not value:
        return "x"
    kind = value.get("kind")
    if kind == "empty":
        return ""
    if kind == "repeat":
        return str(value.get("char", "a")) * int(value.get("count", 0))
    return str(value.get("text", "x"))


async def replay_case(
    rb: RoleBrowser, base_url: str, case: TestCase
) -> tuple[bool, str]:
    page = rb.page
    expect_denied = case.evidence.get("rule") == "role_denied"
    await rb.goto(base_url, fresh=True)
    await rb.settle()
    steps = [s for s in case.steps if s["action"] != "verify"]
    last_click = max(
        (i for i, s in enumerate(steps) if s["action"] == "click"), default=-1
    )
    has_fill = any(s["action"] in {"fill", "select", "clear"} for s in steps)
    note = "needs data: " if case.evidence.get("needs_rows") else ""
    for i, s in enumerate(steps):
        a, label = s["action"], (s.get("target") or {}).get("label", "")
        if a == "open_screen":
            route = str(s.get("screen") or "")
            if route.startswith(("/", "#/")):
                await rb.goto(base_url.rstrip("/") + route, fresh=True)
                await rb.settle()
            continue  # otherwise the start page is already open
        if a in {"fill", "select", "clear"}:
            loc = await field(page, label)
            if loc is None:
                return False, f"{note}field '{label}' not found"
            if a != "select":  # a select is located; its options are app data
                await loc.fill(
                    "" if a == "clear" else text_of(s.get("value")), timeout=5000
                )
        elif a == "click":
            loc = await first(page, label, CLICKABLE)
            if loc is None:
                if expect_denied:
                    return True, ""  # not offered to this role: what the case expects
                return False, f"{note}control '{label}' not found"
            # In a form flow the last click is the submit: located, never pressed.
            if has_fill and i == last_click:
                continue
            await loc.click(timeout=5000)
            await rb.settle()
    if expect_denied:
        return (
            False,
            "every control on the path is offered to a role the case says cannot use it",
        )
    return True, ""


async def main(project: str, base_url: str | None, limit: int, seed: int) -> None:
    with Session(engine) as s:
        p = s.exec(select(Project).where(Project.name == project)).one()
        cases = list(s.exec(select(TestCase).where(TestCase.project_id == p.id)))
        roles = {
            r.name: r for r in s.exec(select(AppRole).where(AppRole.project_id == p.id))
        }
        creds = {n: load_role_credentials(s, r) for n, r in roles.items()}
        recipes = {n: r.login_recipe for n, r in roles.items()}
        for c in cases:
            s.expunge(c)
        target = base_url or p.target_base_url
    random.Random(seed).shuffle(cases)
    cases = [c for c in cases if c.role_name in roles or not c.role_name][:limit]
    by_role: dict[str, list[TestCase]] = {}
    for c in cases:
        signed_out = any("signed out" in str(x) for x in c.preconditions)
        by_role.setdefault(
            "" if signed_out else (c.role_name or sorted(roles)[0]), []
        ).append(c)
    results: list[dict[str, Any]] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for name, group in by_role.items():
            blocked: list[str] = []

            async def on_block(*_args: Any) -> None:
                blocked.append("blocked")

            rb = (
                await RoleBrowser.anonymous(
                    browser, target_url=target, on_block=on_block
                )
                if not name
                else await RoleBrowser.open(
                    browser,
                    target_url=target,
                    role_name=name,
                    recipe=recipes[name],
                    username=creds[name]["username"],
                    password=creds[name]["password"],
                    on_block=on_block,
                    totp_seed=creds[name].get("totp_seed"),
                )
            )
            for c in group:
                try:
                    ok, why = await replay_case(rb, target, c)
                except Exception as exc:
                    ok, why = False, f"{type(exc).__name__}: {str(exc)[:100]}"
                results.append(
                    {
                        "tc": c.tc_key,
                        "type": c.test_type,
                        "origin": c.origin,
                        "ok": ok,
                        "why": why,
                    }
                )
            await rb.close()
        await browser.close()
    ok = sum(1 for r in results if r["ok"])
    by_type: dict[str, list[int]] = {}
    for r in results:
        t = by_type.setdefault(r["type"], [0, 0])
        t[0] += r["ok"]
        t[1] += 1
    out = {
        "replayed": len(results),
        "executable": ok,
        "executable_rate": round(ok / len(results), 3) if results else None,
        "by_type": {k: f"{a}/{b}" for k, (a, b) in by_type.items()},
        "failures": Counter(r["why"] for r in results if not r["ok"]).most_common(10),
        "failed_cases": [r for r in results if not r["ok"]][:20],
    }
    sys.stdout.write(json.dumps(out, indent=2) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--base-url")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    asyncio.run(main(a.project, a.base_url, a.limit, a.seed))
