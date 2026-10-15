"""Credential encryption (Fernet). Secrets are decrypted only inside the worker's auth module."""

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import settings

KEY_VERSION = 1


class CryptoError(Exception):
    pass


def _cipher() -> MultiFernet:
    raw = settings.CREDENTIAL_ENCRYPTION_KEY
    if raw is None or not raw.get_secret_value().strip():
        raise CryptoError("CREDENTIAL_ENCRYPTION_KEY is not configured")
    keys = [k.strip() for k in raw.get_secret_value().split(",") if k.strip()]
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except ValueError as exc:
        raise CryptoError(
            "CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key"
        ) from exc


def encrypt_json(data: dict[str, Any]) -> str:
    return _cipher().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str) -> dict[str, Any]:
    try:
        return json.loads(_cipher().decrypt(token.encode()))  # type: ignore[no-any-return]
    except InvalidToken as exc:
        raise CryptoError("credential could not be decrypted (wrong key?)") from exc
