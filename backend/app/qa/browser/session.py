"""One logged-in browser context per application role."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Dialog, Page, Route

from app.qa.browser.auth import login
from app.qa.browser.extract import DEFAULT_IGNORE_NAME_RE, Snapshot, extract
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
    ) -> None:
        self.fp_ignore = fp_ignore
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
    ) -> RoleBrowser:
        context = await browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]}
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
        )
        page.on("dialog", lambda d: asyncio.ensure_future(rb._dismiss(d)))
        context.on("page", lambda p: asyncio.ensure_future(rb._close_stray(p)))
        try:
            await login(
                page,
                target_url,
                recipe,
                username=username,
                password=password,
                settle=rb.settle,
            )
        except BaseException:
            await context.close()
            raise
        guard.arm()  # from here on the app can only be read, never changed
        return rb

    # --- housekeeping -------------------------------------------------
    @staticmethod
    async def _dismiss(dialog: Dialog) -> None:
        try:
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
        self.current = await extract(
            self.page, ignore_selectors=self.ignore_selectors, fp_ignore=self.fp_ignore
        )
        return self.current

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
            await self.page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception as exc:
            raise ActionError(f"navigation failed: {type(exc).__name__}") from exc
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
