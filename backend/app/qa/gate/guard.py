"""Network-level mutation guard: in safe mode, state-changing requests are aborted regardless of what the agent clicked."""

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import urlsplit

READ_METHODS = {"GET", "HEAD", "OPTIONS"}

# Realtime transports use POST to *carry messages*, not to change application data. Blocking them
# starves the app of its live channel and floods the log, so they are allowed by default.
DEFAULT_SAFE_POST_PATTERNS = [
    r"/socket\.io/",
    r"/sockjs[-/]",
    r"/signalr/",
    r"/hub/negotiate",
    r"/__webpack_hmr",
]


class _Request(Protocol):
    method: str
    url: str
    post_data: str | None


class _Route(Protocol):
    request: _Request

    async def abort(self, error_code: str | None = None) -> None: ...
    async def continue_(self) -> None: ...


BlockCallback = Callable[[str, str, str], Awaitable[None]]  # (method, url, reason)


def is_graphql_mutation(body: str | None) -> bool:
    if not body:
        return False
    try:
        data: Any = json.loads(body)
    except ValueError:
        return False
    items = data if isinstance(data, list) else [data]
    return any(
        isinstance(i, dict)
        and isinstance(i.get("query"), str)
        and re.match(r"\s*mutation\b", i["query"])
        for i in items
    )


class RequestGuard:
    def __init__(
        self,
        on_block: BlockCallback,
        *,
        safe_post_patterns: list[str] | None = None,
        app_origin: str | None = None,
    ) -> None:
        self.armed = False
        self._on_block = on_block
        self._safe = [
            re.compile(p)
            for p in [*DEFAULT_SAFE_POST_PATTERNS, *(safe_post_patterns or [])]
        ]
        self._origin = app_origin

    def arm(self) -> None:
        self.armed = True

    def disarm(self) -> None:
        self.armed = False

    def _violates(self, request: _Request) -> str | None:
        if request.method in READ_METHODS:
            return None
        if any(p.search(request.url) for p in self._safe):
            return None  # an explicit allow (project config or a known realtime transport) wins
        if self._origin and urlsplit(request.url).netloc != self._origin:
            # Not the application under test (analytics, third-party widgets): never let a crawl
            # write to other systems either.
            return "external non-read request"
        return f"non-read method {request.method}"

    async def handle(self, route: _Route) -> None:
        """Playwright route handler: `await context.route('**/*', guard.handle)`."""
        request = route.request
        reason = self._violates(request) if self.armed else None
        if (
            self.armed
            and reason is None
            and request.method == "POST"
            and is_graphql_mutation(request.post_data)
        ):
            reason = "graphql mutation"
        if reason is None:
            await route.continue_()
            return
        await self._on_block(request.method, request.url, reason)
        await route.abort()
