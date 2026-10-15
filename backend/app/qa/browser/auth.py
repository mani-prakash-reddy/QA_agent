"""Authentication. This is the ONLY code that handles role credentials; agents and LLMs never see them."""

import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Page
from sqlmodel import Session, select

from app.qa.models import AppRole, RoleCredential
from app.qa.security.crypto import decrypt_json

LOGIN_BUTTON_RE = re.compile(r"log ?in|sign ?in|submit|continue|next", re.I)
# Only wording that dismisses a banner. Never anything like "Accept order", "Delete", "Confirm".
BLOCKER_RE = re.compile(
    r"^(close( welcome banner)?|dismiss( cookie message)?|accept( all)?( cookies)?|got it|"
    r"i agree|me want it!?|no thanks|maybe later)$",
    re.I,
)
LOGIN_TIMEOUT_S = 15


class AuthError(Exception):
    """Login failed. The message never contains credentials."""


def load_role_credentials(session: Session, role: AppRole) -> dict[str, str]:
    row = session.exec(
        select(RoleCredential).where(RoleCredential.role_id == role.id)
    ).first()
    if row is None:
        raise AuthError(f"role '{role.name}' has no credentials")
    return {k: str(v) for k, v in decrypt_json(row.ciphertext).items()}


async def _visible_error_text(page: Page) -> str:
    try:
        text = await page.evaluate(
            """() => [...document.querySelectorAll('[role=alert],[aria-live],.error,[class*=error],[class*=destructive]')]
                 .map(e => (e.innerText||'').trim()).filter(Boolean).join(' | ').slice(0, 200)"""
        )
        return str(text)
    except Exception:
        return ""


async def dismiss_blockers(page: Page, rounds: int = 4) -> int:
    """Close cookie banners / welcome dialogs that cover the page. Returns how many were closed."""
    closed = 0
    for _ in range(rounds):
        buttons = page.get_by_role("button", name=BLOCKER_RE)
        progressed = False
        for i in range(await buttons.count()):
            button = buttons.nth(i)
            try:
                if await button.is_visible():
                    await button.click(timeout=2000)
                    await page.wait_for_timeout(300)
                    closed += 1
                    progressed = True
            except Exception:
                continue
        if not progressed:
            break
    return closed


async def login(
    page: Page,
    target_url: str,
    recipe: dict[str, Any],
    *,
    username: str,
    password: str,
    settle: Callable[..., Awaitable[None]],
) -> None:
    mfa = (recipe.get("mfa") or "none").lower()
    if mfa != "none":
        raise AuthError(f"MFA type '{mfa}' is not supported yet")

    login_url = urljoin(target_url, recipe.get("login_url") or "/login")
    try:
        await page.goto(login_url, wait_until="domcontentloaded", timeout=20000)
    except Exception as exc:
        raise AuthError(
            f"could not open the login page ({type(exc).__name__})"
        ) from exc
    await settle()
    await dismiss_blockers(page)

    password_box = page.locator(
        recipe.get("password_selector") or "input[type=password]:visible"
    ).first
    if not await password_box.count():
        raise AuthError("no login form found (set login_recipe.password_selector)")
    # The username field is the text input in the password field's own form; the first input on the
    # page may be a toolbar search box.
    text_inputs = (
        "input:visible:not([type=password]):not([type=hidden]):not([type=checkbox])"
        ":not([type=radio]):not([type=search])"
    )
    own_form = password_box.locator("xpath=ancestor::form[1]")
    scope = own_form if await own_form.count() else page
    username_box = (
        page.locator(recipe["username_selector"]).first
        if recipe.get("username_selector")
        else scope.locator(text_inputs).first
    )
    if not await username_box.count():
        raise AuthError("no username field found (set login_recipe.username_selector)")

    await username_box.fill(username)
    await password_box.fill(password)

    # Prefer the submit button of the password field's own form ("Log in" beats "Log in with Google").
    submit = (
        page.locator(recipe["submit_selector"]).first
        if recipe.get("submit_selector")
        else (
            password_box.locator("xpath=ancestor::form[1]")
            .locator("button[type=submit],input[type=submit]")
            .first
        )
    )
    if not await submit.count():
        submit = page.get_by_role("button", name=LOGIN_BUTTON_RE).first
    if await submit.count():
        await submit.click()
    else:
        await password_box.press("Enter")

    login_path = urlsplit(login_url).path
    loop_deadline = LOGIN_TIMEOUT_S
    waited = 0.0
    while waited < loop_deadline:
        await page.wait_for_timeout(250)
        waited += 0.25
        still_form = await page.locator("input[type=password]:visible").count()
        if not still_form or urlsplit(page.url).path != login_path:
            await settle()
            await dismiss_blockers(page)
            return
    detail = await _visible_error_text(page)
    raise AuthError(f"login did not succeed{': ' + detail if detail else ''}")
