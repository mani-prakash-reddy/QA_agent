"""RFC 6238 time-based one-time passwords (stdlib only). The seed never leaves the process."""

import base64
import hashlib
import hmac
import struct
import time


def totp(
    seed: str, *, at: float | None = None, digits: int = 6, period: int = 30
) -> str:
    cleaned = seed.strip().replace(" ", "").replace("-", "").upper()
    key = base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
    counter = int((time.time() if at is None else at) // period)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF) % 10**digits
    return str(code).zfill(digits)


def seconds_left(period: int = 30, *, at: float | None = None) -> float:
    return period - ((time.time() if at is None else at) % period)
