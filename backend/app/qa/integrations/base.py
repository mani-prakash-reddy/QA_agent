"""Common shape of a test-management connector. The user's own credentials (bring your own key) are
passed in for one request; nothing here stores or logs them."""

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx


class IntegrationError(Exception):
    """Reported to the user. Never contains the credential."""


@dataclass
class CasePayload:
    tc_key: str
    title: str
    requirement: str
    priority: str  # P1..P4
    test_type: str
    module: str
    role: str
    preconditions: list[str]
    steps: list[str]
    expected: str
    basis: str  # how the expected result is known: observed | inferred | requirement | not_observed
    labels: list[str] = field(default_factory=list)


@dataclass
class Pushed:
    external_id: str
    url: str = ""


def strip_numbering(step: str) -> str:
    return re.sub(r"^\s*\d+[.)]\s*", "", step).strip()


def label_safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-")[:60]


class Connector(ABC):
    platform: str = ""

    def __init__(
        self,
        base_url: str,
        username: str,
        secret: str,
        settings: dict[str, Any] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username, self._secret = username, secret
        self.settings = settings or {}
        self._client = client
        self._own = client is None

    # --- http -------------------------------------------------------------------------
    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=30, follow_redirects=False)
        return self._client

    def close(self) -> None:
        if self._own and self._client is not None:
            self._client.close()

    def _auth(self) -> httpx.Auth | None:
        return None

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json"}

    def request(self, method: str, url: str, **kw: Any) -> Any:
        """One call, with a polite wait on rate limiting and errors that explain themselves."""
        for attempt in range(4):
            try:
                resp = self._http().request(
                    method, url, headers=self._headers(), auth=self._auth(), **kw
                )
            except httpx.HTTPError as exc:
                raise IntegrationError(
                    f"could not reach {self.platform} ({type(exc).__name__})"
                ) from exc
            if resp.status_code == 429 and attempt < 3:
                time.sleep(min(10.0, float(resp.headers.get("Retry-After") or 2)))
                continue
            break
        if resp.status_code in {401, 403}:
            raise IntegrationError(
                f"{self.platform} refused the credentials or the permission (HTTP {resp.status_code})"
            )
        if resp.is_redirect:
            raise IntegrationError(
                f"{self.platform} redirected the request: check the site URL"
            )
        if resp.status_code >= 400:
            raise IntegrationError(
                f"{self.platform} answered HTTP {resp.status_code}: {self._scrub(resp.text)[:300]}"
            )
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    def _scrub(self, text: str) -> str:
        return text.replace(self._secret, "***") if self._secret else text

    # --- what a connector does ----------------------------------------------------------------
    @abstractmethod
    def test(self) -> str:
        """Prove the credentials work; returns a short description ("signed in as ...")."""

    @abstractmethod
    def options(self, kind: str, parent: str = "") -> list[dict[str, str]]:
        """Choices for the destination picker: [{id, name}]."""

    @abstractmethod
    def destination_key(self, destination: dict[str, Any]) -> str:
        """A stable string for where cases go (so a re-push finds its earlier push)."""

    @abstractmethod
    def create(self, destination: dict[str, Any], case: CasePayload) -> Pushed: ...

    @abstractmethod
    def update(
        self, destination: dict[str, Any], external_id: str, case: CasePayload
    ) -> Pushed: ...
