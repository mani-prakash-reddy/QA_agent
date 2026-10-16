"""Authentication. This is the ONLY code that handles role credentials; agents and LLMs never see them."""

import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Page
from sqlmodel import Session, select

from app.qa.browser.extract import route_path
from app.qa.models import AppRole, RoleCredential
from app.qa.security.crypto import decrypt_json
from app.qa.security.totp import seconds_left, totp

LOGIN_BUTTON_RE = re.compile(r"log ?in|sign ?in|submit|continue|next", re.I)
# Only wording that dismisses a banner. Never anything like "Accept order", "Delete", "Confirm".
BLOCKER_RE = re.compile(
    r"^(close( welcome banner)?|dismiss( cookie message)?|accept( all)?( cookies)?|got it|"
    r"i agree|me want it!?|no thanks|maybe later)$",
    re.I,
)
LOGIN_TIMEOUT_S = 15
OTP_BUTTON_RE = re.compile(
    r"verify|confirm|submit|continue|log ?in|sign ?in|next", re.I
)
CAPTCHA_MESSAGE = (
    "a CAPTCHA protects this login and cannot be solved automatically. Log in by hand, export the "
    "browser session and set login_recipe.mfa to 'storage_state' with the session in the credentials"
)
OTP_FIELD = (
    "input[autocomplete=one-time-code]:visible, input[name*=otp i]:visible, input[id*=otp i]:visible,"
    " input[name*=totp i]:visible, input[name*=mfa i]:visible, input[name*=token i]:visible,"
    " input[placeholder*=code i]:visible, input[aria-label*=code i]:visible,"
    " input[aria-label*=authenticat i]:visible"
)
CAPTCHA_MARKERS = (
    "iframe[src*=recaptcha], iframe[src*=hcaptcha], iframe[src*=turnstile],"
    " .g-recaptcha, .h-captcha, .cf-turnstile, [data-sitekey]"
)
HUMAN_CODE_TIMEOUT_S = 240
FALLBACK_LOGIN_PATHS = [
    "/#/login",  # hash-routed single-page apps (Angular, Juice Shop)
    "/#/signin",
    "/user/login",
    "/users/sign_in",
    "/signin",
    "/sign-in",
    "/account/login",
    "/auth/login",
    "/users/login",
    "/login.php",
]

# Asks a person for a one-time code (the run page shows a prompt); returns the code they typed.
HumanCode = Callable[[str], Awaitable[str]]


class AuthError(Exception):
    """Login failed. The message never contains credentials."""


class CaptchaError(AuthError):
    pass


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


async def _has_captcha(page: Page) -> bool:
    try:
        return await page.locator(CAPTCHA_MARKERS).count() > 0
    except Exception:
        return False


async def _answer_otp_step(
    page: Page,
    *,
    mfa: str,
    totp_seed: str | None,
    human_code: HumanCode | None,
) -> None:
    box = page.locator(OTP_FIELD).first
    if mfa == "totp":
        if not totp_seed:
            raise AuthError("the login asks for a code but this role has no TOTP seed")
        if seconds_left() < 4:  # a code about to expire is rejected on arrival
            await page.wait_for_timeout(int((seconds_left() + 0.5) * 1000))
        code = totp(totp_seed)
    elif mfa == "otp_human":
        if human_code is None:
            raise AuthError(
                "the login asks for a one-time code and nobody can be asked (run it from the UI)"
            )
        code = await human_code("one-time code")
    else:
        raise AuthError(
            "the login asks for a one-time code; set login_recipe.mfa to 'totp' (with a seed) or 'otp_human'"
        )
    await box.fill(code)
    own_form = box.locator("xpath=ancestor::form[1]")
    scope = own_form if await own_form.count() else page
    button = scope.locator("button[type=submit],input[type=submit]").first
    if not await button.count():
        button = page.get_by_role("button", name=OTP_BUTTON_RE).first
    if await button.count():
        await button.click()
    else:
        await box.press("Enter")


async def login(
    page: Page,
    target_url: str,
    recipe: dict[str, Any],
    *,
    username: str,
    password: str,
    settle: Callable[..., Awaitable[None]],
    totp_seed: str | None = None,
    human_code: HumanCode | None = None,
    on_login_page: Callable[[Page], Awaitable[None]] | None = None,
) -> None:
    mfa = (recipe.get("mfa") or "none").lower()
    if mfa not in {"none", "totp", "otp_human"}:
        raise AuthError(f"MFA type '{mfa}' is not supported")

    # The configured login page, then the usual ones: apps differ (/login, /user/login, /users/sign_in...).
    wanted = recipe.get("login_url")
    candidates = [wanted] if wanted else ["/login", *FALLBACK_LOGIN_PATHS]
    login_url = urljoin(target_url, candidates[0])
    for i, cand in enumerate(candidates):
        url = urljoin(target_url, cand)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception as exc:
            if i == len(candidates) - 1:
                raise AuthError(
                    f"could not open the login page ({type(exc).__name__})"
                ) from exc
            continue
        await settle()
        await dismiss_blockers(page)
        if await _has_captcha(page):
            raise CaptchaError(CAPTCHA_MESSAGE)
        if await page.locator(
            recipe.get("password_selector") or "input[type=password]:visible"
        ).count():
            login_url = url
            if not wanted:
                parts = urlsplit(
                    url
                )  # keep a #/route: it is the route of a hash-routed app
                recipe["login_url"] = (
                    parts.path
                    + (f"?{parts.query}" if parts.query else "")
                    + (f"#{parts.fragment}" if parts.fragment else "")
                )  # later re-logins go straight there
            break

    password_box = page.locator(
        recipe.get("password_selector") or "input[type=password]:visible"
    ).first
    if not await password_box.count():
        raise AuthError("no login form found (set login_recipe.password_selector)")
    if (
        on_login_page is not None
    ):  # the sign-in screen is part of the application: record it, untouched
        try:
            await on_login_page(page)
        except Exception:  # recording it must never break signing in
            pass
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

    login_path = route_path(login_url)
    waited = 0.0
    otp_attempts = 0
    while waited < LOGIN_TIMEOUT_S:
        await page.wait_for_timeout(250)
        waited += 0.25
        if await page.locator(OTP_FIELD).count():
            if otp_attempts >= 2:
                raise AuthError("the one-time code was rejected twice")
            await _answer_otp_step(
                page,
                mfa=mfa,
                totp_seed=totp_seed,
                human_code=human_code,
            )
            otp_attempts += 1
            waited = 0.0  # a person typing a code must not eat the login budget
            await page.wait_for_timeout(500)
            continue
        if await _has_captcha(page):
            raise CaptchaError(CAPTCHA_MESSAGE)
        still_form = await page.locator("input[type=password]:visible").count()
        if not still_form or route_path(page.url) != login_path:
            await settle()
            await dismiss_blockers(page)
            return
    detail = await _visible_error_text(page)
    raise AuthError(f"login did not succeed{': ' + detail if detail else ''}")
