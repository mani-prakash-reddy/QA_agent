"""Shared helpers for the demo applications: users from the environment, signed-in sessions, TOTP.

The demo accounts are *not* in this code: they come from the DEMO_USERS environment variable (compose sets
them for local use), a JSON list of {"email", "password", "role", "name", "totp_seed"?}."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
from typing import Any

DEFAULT_USERS = (
    '[{"email":"admin@demo.test","password":"Admin-Demo-1","role":"admin","name":"Alex Admin"},'
    '{"email":"user@demo.test","password":"User-Demo-1","role":"user","name":"Uma User"}]'
)


def users() -> list[dict[str, Any]]:
    return list(json.loads(os.environ.get("DEMO_USERS", DEFAULT_USERS)))


def totp(seed: str, at: float | None = None) -> str:
    key = base64.b32decode(seed.strip().upper() + "=" * (-len(seed.strip()) % 8))
    mac = hmac.new(key, struct.pack(">Q", int((at or time.time()) // 30)), hashlib.sha1).digest()
    o = mac[-1] & 15
    return str((struct.unpack(">I", mac[o : o + 4])[0] & 0x7FFFFFFF) % 10**6).zfill(6)


def totp_ok(seed: str, code: str) -> bool:
    now = time.time()
    return any(totp(seed, now + d) == code for d in (-30, 0, 30))


class Sessions:
    """In-memory sessions with an optional time to live (the crawler must cope with expiry)."""

    def __init__(self, ttl: int | None = None) -> None:
        self.ttl = ttl
        self._s: dict[str, tuple[dict[str, Any], float]] = {}

    def create(self, user: dict[str, Any]) -> str:
        token = secrets.token_urlsafe(24)
        self._s[token] = (user, time.time())
        return token

    def get(self, token: str | None) -> dict[str, Any] | None:
        if not token or token not in self._s:
            return None
        user, born = self._s[token]
        if self.ttl and time.time() - born > self.ttl:
            self._s.pop(token, None)
            return None
        return user

    def drop(self, token: str | None) -> None:
        if token:
            self._s.pop(token, None)
