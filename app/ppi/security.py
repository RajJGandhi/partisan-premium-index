from __future__ import annotations

import hmac
import ipaddress
import socket
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests

from app.config import get_settings

TRACKING_QUERY_PREFIXES = ("utm_", "fbclid", "gclid", "mc_")


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith(TRACKING_QUERY_PREFIXES)
    ]
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path.rstrip("/") or "/", urlencode(query), ""))


def _is_private_host(host: str) -> bool:
    if host.lower() in {"localhost", "localhost.localdomain"}:
        return True
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return False
    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


def validate_external_url(url: str, allowed_domains: set[str] | None = None) -> str:
    settings = get_settings()
    parsed = urlsplit(url.strip())
    allowed_schemes = {x.strip() for x in settings.safe_source_schemes.split(",") if x.strip()}
    if parsed.scheme not in allowed_schemes:
        raise ValueError("Only approved URL schemes are permitted")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Invalid external URL")
    host = parsed.hostname.lower().rstrip(".")
    domains = allowed_domains if allowed_domains is not None else settings.source_domain_allowlist
    if domains and not any(host == d or host.endswith("." + d) for d in domains):
        raise ValueError("Domain is not in the source allowlist")
    if _is_private_host(host):
        raise ValueError("Private or internal addresses are not permitted")
    return canonicalize_url(url)


def verify_plaintext_password(candidate: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(candidate.encode(), expected.encode())


def verify_password(candidate: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        import bcrypt

        return bcrypt.checkpw(candidate.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def safe_get(
    url: str,
    *,
    allowed_domains: set[str] | None = None,
    timeout: float = 20,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, Any] | None = None,
    max_redirects: int = 4,
) -> requests.Response:
    """Perform an SSRF-guarded GET and validate every redirect target."""
    current = validate_external_url(url, allowed_domains)
    current_params = params
    for _ in range(max_redirects + 1):
        response = requests.get(
            current,
            params=current_params,
            timeout=timeout,
            headers=dict(headers or {}),
            allow_redirects=False,
        )
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        location = response.headers.get("Location")
        if not location:
            return response
        current = validate_external_url(urljoin(current, location), allowed_domains)
        current_params = None
    raise ValueError("Too many redirects while fetching external source")
