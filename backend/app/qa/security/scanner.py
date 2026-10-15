"""Secret scanner: guarantees known secret values never reach an LLM, event stream or log."""

from collections.abc import Iterable

REDACTED = "[REDACTED]"
MIN_SECRET_LENGTH = 4  # shorter values would cause constant false positives


class SecretLeakError(Exception):
    """Raised when outgoing content contains a known secret value."""


class SecretScanner:
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets: set[str] = set()
        self.add(secrets)

    def add(self, secrets: Iterable[str]) -> None:
        self._secrets |= {s for s in secrets if s and len(s) >= MIN_SECRET_LENGTH}

    def contains_secret(self, text: str) -> bool:
        return any(s in text for s in self._secrets)

    def assert_clean(self, text: str, where: str) -> None:
        if self.contains_secret(text):
            # Never include the secret itself in the message.
            raise SecretLeakError(f"secret value detected in {where}")

    def redact(self, text: str) -> str:
        # Longest first so a secret that contains another is fully replaced.
        for s in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(s, REDACTED)
        return text
