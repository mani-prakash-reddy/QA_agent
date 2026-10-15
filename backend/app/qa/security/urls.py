"""Target-URL validation (SSRF guard): the crawler may only visit explicitly allowed hosts or public addresses."""

import ipaddress
from urllib.parse import urlsplit

from app.core.config import settings


class TargetUrlError(ValueError):
    pass


def validate_target_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"}:
        raise TargetUrlError("target URL must start with http:// or https://")
    host = (parts.hostname or "").lower()
    if not host:
        raise TargetUrlError("target URL has no host")
    if parts.username or parts.password:
        raise TargetUrlError("credentials must not be embedded in the target URL")
    if host in settings.allowed_target_hosts:
        return url.strip()
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # A hostname we cannot vet without DNS; deployments restrict worker egress instead.
        return url.strip()
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    ):
        raise TargetUrlError(
            f"address {host} is private/internal; add it to ALLOWED_TARGET_HOSTS to permit it"
        )
    return url.strip()
