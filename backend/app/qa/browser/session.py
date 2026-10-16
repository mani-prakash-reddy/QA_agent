"""One logged-in browser context per application role."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Browser, BrowserContext, Dialog, Page, Route

from app.qa.browser.auth import AuthError, HumanCode, login
from app.qa.browser.extract import (
    DEFAULT_IGNORE_NAME_RE,
    Snapshot,
    extract,
    route_path,
)
from app.qa.gate.guard import RequestGuard

logger = logging.getLogger(__name__)

BlockHook = Callable[[str, str, str], Awaitable[None]]  # (method, url, reason)


def _why(exc: Exception) -> str:
    """Playwright puts the reason ("element is not enabled", "<div> intercepts pointer events")
    in the call log, not the first line."""
    lines = [ln.strip(" -") for ln in str(exc).splitlines() if ln.strip()]
    head = lines[0] if lines else type(exc).__name__
    reasons = [
        ln
        for ln in lines[1:]
        if not ln.startswith(("Call log", "waiting", "retrying", "attempting"))
    ]
    return (
        head + (" | " + " ; ".join(dict.fromkeys(reasons[-2:])) if reasons else "")
    )[:300]


MAX_SESSION_REFRESHES = 6
SessionHook = Callable[[str], Awaitable[None]]  # (what happened)


@dataclass
class LoginContext:
    """What a re-login needs. Lives only in this process, like the credentials themselves."""

    target_url: str
    recipe: dict[str, Any]
    username: str
    password: str
    totp_seed: str | None = None
    human_code: HumanCode | None = None
    storage_state: dict[str, Any] | None = None
    on_login_page: Callable[[Page], Awaitable[None]] | None = None


class ActionError(Exception):
    """A browser action failed (element gone, not clickable, ...). Reported to the agent as data."""


class RoleBrowser:
    def __init__(
        self,
        context: BrowserContext,
        page: Page,
        guard: RequestGuard,
        *,
        role_name: str,
        ignore_selectors: list[str],
        fp_ignore: str = "",
        login_ctx: LoginContext | None = None,
        on_session: SessionHook | None = None,
    ) -> None:
        self.fp_ignore = fp_ignore
        self.login_ctx = login_ctx
        self.on_session = on_session
        self.session_refreshes = 0
        self.last_good_url: str | None = None
        self.last_status: int | None = None  # HTTP status of the last document load
        self.context, self.page, self.guard = context, page, guard
        self.role_name = role_name
        self.ignore_selectors = ignore_selectors
        self.current: Snapshot | None = None
        self.seen_chrome: set[str] = (
            set()
        )  # nav/header/footer keys already shown to the agent
        self.stray_pages_closed = 0

    @classmethod
    async def open(
        cls,
        browser: Browser,
        *,
        target_url: str,
        role_name: str,
        recipe: dict[str, Any],
        username: str,
        password: str,
        on_block: BlockHook,
        safe_post_patterns: list[str] | None = None,
        ignore_selectors: list[str] | None = None,
        fp_ignore: str = "",
        viewport: tuple[int, int] = (1280, 800),
        totp_seed: str | None = None,
        human_code: HumanCode | None = None,
        storage_state: dict[str, Any] | None = None,
        on_session: SessionHook | None = None,
        on_login_page: Callable[[Page], Awaitable[None]] | None = None,
    ) -> RoleBrowser:
        context = await browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]},
            **({"storage_state": storage_state} if storage_state else {}),  # type: ignore[arg-type]
        )
        guard = RequestGuard(
            on_block,
            safe_post_patterns=safe_post_patterns,
            app_origin=urlsplit(target_url).netloc,
        )

        async def _route(route: Route) -> None:
            await guard.handle(route)  # type: ignore[arg-type]

        await context.route("**/*", _route)  # unarmed during login
        page = await context.new_page()
        rb = cls(
            context,
            page,
            guard,
            role_name=role_name,
            ignore_selectors=ignore_selectors or [],
            fp_ignore=fp_ignore,
            login_ctx=LoginContext(
                target_url,
                recipe,
                username,
                password,
                totp_seed,
                human_code,
                storage_state,
                on_login_page,
            ),
            on_session=on_session,
        )
        page.on("dialog", lambda d: asyncio.ensure_future(rb._dismiss(d)))
        context.on("page", lambda p: asyncio.ensure_future(rb._close_stray(p)))
        try:
            if storage_state:
                await rb._enter_with_stored_session()
            else:
                await rb._login()
        except BaseException:
            await context.close()
            raise
        guard.arm()  # from here on the app can only be read, never changed
        return rb

    @classmethod
    async def anonymous(
        cls, browser: Browser, *, target_url: str, on_block: BlockHook
    ) -> RoleBrowser:
        """A browser that is not signed in (to replay what a signed-out visitor sees). Writes stay blocked."""
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        guard = RequestGuard(on_block, app_origin=urlsplit(target_url).netloc)

        async def _route(route: Route) -> None:
            await guard.handle(route)  # type: ignore[arg-type]

        await context.route("**/*", _route)
        page = await context.new_page()
        rb = cls(context, page, guard, role_name="(signed out)", ignore_selectors=[])
        page.on("dialog", lambda d: asyncio.ensure_future(rb._dismiss(d)))
        guard.arm()
        return rb

    # --- authentication -------------------------------------------------
    async def _login(self) -> None:
        lc = self.login_ctx
        assert lc is not None
        await login(
            self.page,
            lc.target_url,
            lc.recipe,
            username=lc.username,
            password=lc.password,
            settle=self.settle,
            totp_seed=lc.totp_seed,
            human_code=lc.human_code,
            on_login_page=lc.on_login_page,
        )

    def _login_route(self) -> str:
        lc = self.login_ctx
        assert lc is not None
        return route_path(
            urljoin(lc.target_url, lc.recipe.get("login_url") or "/login")
        )

    async def _on_login_page(self) -> bool:
        try:
            if route_path(self.page.url) != self._login_route():
                return False
            return await self.page.locator("input[type=password]:visible").count() > 0
        except Exception:
            return False

    async def _enter_with_stored_session(self) -> None:
        """A session exported from a real browser (the way past CAPTCHA/SSO): open the app with it."""
        lc = self.login_ctx
        assert lc is not None
        await self.page.goto(
            lc.target_url, wait_until="domcontentloaded", timeout=20000
        )
        await self.settle()
        if await self._on_login_page():
            raise AuthError(
                "the stored browser session is no longer valid; export a fresh one"
            )

    async def refresh_session(self) -> bool:
        """Called when a page turned out to be the login page. Signs in again (the guard is lifted
        only for the duration of the login) and reports whether the session is back."""
        lc = self.login_ctx
        if lc is None or lc.storage_state:
            return False  # a stored session cannot be renewed by us
        if self.session_refreshes >= MAX_SESSION_REFRESHES:
            raise AuthError(
                "the session keeps expiring; stopped after repeated re-logins"
            )
        self.session_refreshes += 1
        was_armed = self.guard.armed
        self.guard.disarm()
        try:
            await self._login()
        finally:
            if was_armed:
                self.guard.arm()
        if self.on_session:
            await self.on_session("session_refreshed")
        return True

    # --- housekeeping -------------------------------------------------
    @staticmethod
    async def _dismiss(dialog: Dialog) -> None:
        try:
            if dialog.type == "beforeunload":
                # "Leave site? Changes may be lost": the probe typed into fields on purpose and never saves,
                # so leaving is right. Staying would make every later navigation fail.
                await dialog.accept()
            else:
                await dialog.dismiss()  # confirm() -> cancel: the safe answer
        except Exception:
            pass

    async def _close_stray(self, popup: Page) -> None:
        if popup is self.page:
            return
        self.stray_pages_closed += 1
        try:
            await popup.close()
        except Exception:
            pass

    async def close(self) -> None:
        try:
            await self.context.close()
        except Exception:
            logger.debug("context close failed", exc_info=True)

    # --- observation --------------------------------------------------
    async def settle(self, timeout: float = 6.0, quiet: float = 0.36) -> None:
        """Wait until URL, heading and DOM size stop changing (networkidle alone leaves stale headings)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            await self.page.wait_for_load_state(
                "networkidle", timeout=min(700, timeout * 1000)
            )
        except Exception:
            pass
        sig_js = (
            "() => [location.href, ((document.querySelector('h1')||{}).innerText||''),"
            " document.body ? document.body.innerText.length : 0,"
            " document.querySelectorAll('[role=dialog],[role=menu],[role=listbox]').length].join('|')"
        )
        last, stable_for = None, 0.0
        while loop.time() < deadline:
            try:
                sig = await self.page.evaluate(sig_js)
            except Exception:  # navigation in flight
                sig = None
            if sig is not None and sig == last:
                stable_for += 0.12
                if stable_for >= quiet:
                    return
            else:
                stable_for, last = 0.0, sig
            await asyncio.sleep(0.12)

    async def snapshot(self) -> Snapshot:
        snap = await extract(
            self.page, ignore_selectors=self.ignore_selectors, fp_ignore=self.fp_ignore
        )
        if (
            self.last_good_url
            and self.login_ctx
            and route_path(snap.url) == self._login_route()
            and route_path(self.last_good_url) != self._login_route()
            and any(e.input_type == "password" for e in snap.elements)
            and await self.refresh_session()
        ):
            # The app signed us out (session timeout): sign in again and return to where we were.
            await self.goto(self.last_good_url)
            snap = await extract(
                self.page,
                ignore_selectors=self.ignore_selectors,
                fp_ignore=self.fp_ignore,
            )
        if not (self.login_ctx and route_path(snap.url) == self._login_route()):
            self.last_good_url = snap.url
        self.current = snap
        return snap

    async def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=str(path), full_page=False)

    # --- actions ------------------------------------------------------
    def _locator(self, handle: str) -> Any:
        return self.page.locator(f'[data-qa-h="{handle}"]')

    async def goto(self, url: str, *, fresh: bool = False) -> None:
        """Open a URL. `fresh` forces a real reload: navigating to the same document with only a
        different #fragment does not reset anything (an open drawer or backdrop would stay)."""
        try:
            if fresh:
                await self.page.goto("about:blank")
            resp = await self.page.goto(
                url, wait_until="domcontentloaded", timeout=20000
            )
            self.last_status = resp.status if resp else None
        except Exception as exc:
            raise ActionError(f"navigation failed: {_why(exc)}") from exc
        await self.settle()
        if (
            self.login_ctx
            and route_path(url) != self._login_route()
            and await self._on_login_page()
            and await self.refresh_session()
        ):
            # Redirected to the login page: the session expired. Sign in again, then open what was asked.
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except Exception as exc:
                raise ActionError(f"navigation failed: {_why(exc)}") from exc
            await self.settle()

    async def click(self, handle: str) -> None:
        try:
            await self._locator(handle).click(timeout=5000)
        except Exception as exc:
            raise ActionError(
                f"click failed: {str(exc).splitlines()[0][:150]}"
            ) from exc
        await self.settle()

    async def fill(self, handle: str, value: str) -> None:
        try:
            await self._locator(handle).fill(value, timeout=5000)
        except Exception as exc:
            raise ActionError(f"fill failed: {_why(exc)}") from exc

    async def select(self, handle: str, value: str) -> None:
        try:
            await self._locator(handle).select_option(label=value, timeout=5000)
        except Exception as exc:
            raise ActionError(f"select failed: {_why(exc)}") from exc
        await self.settle(timeout=2.0)

    async def click_advancing(self, handle: str) -> None:
        """Press a multi-step form's Next: the guard lets the step's own same-origin request through."""
        self.guard.advancing = True
        try:
            await self.click(handle)
        finally:
            self.guard.advancing = False

    async def press(self, key: str) -> None:
        await self.page.keyboard.press(key)
        await self.settle(timeout=3.0)

    async def back(self) -> None:
        await self.page.go_back(wait_until="domcontentloaded", timeout=15000)
        await self.settle()

    async def blur_active(self) -> None:
        await self.page.evaluate(
            "() => document.activeElement && document.activeElement.blur()"
        )


def default_ignore_selectors() -> list[str]:
    return []


__all__ = ["ActionError", "DEFAULT_IGNORE_NAME_RE", "RoleBrowser"]
