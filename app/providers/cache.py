"""Response cache + last-known-good store for the provider layer (spec sections 5, 31).

Backed by the append-only ``provider_cache`` table. A fetch:

1. looks for a row with the same ``cache_key`` newer than ``ttl`` -> cache hit;
2. on a live miss/failure, falls back to the newest ``ok`` row for the same *request* -- the same
   ``cache_key`` if one is given (so a per-race provider never serves another race's response),
   otherwise the newest for the ``endpoint_family`` -> last-known-good (served with an explicit
   ``STALE`` status, never as if fresh).

Nothing is ever updated in place; every fetch appends a row.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models_quant import ProviderCache


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_cache_key(provider_name: str, method: str, url: str, params: dict | None) -> str:
    material = json.dumps(
        {"p": provider_name, "m": method.upper(), "u": url, "q": params or {}},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def content_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def get_fresh(session: Session, cache_key: str, ttl_minutes: int) -> Optional[ProviderCache]:
    """Newest cached row for this exact request, if within TTL and ``ok``."""
    if ttl_minutes <= 0:
        return None
    cutoff = _utcnow() - timedelta(minutes=ttl_minutes)
    row = session.execute(
        select(ProviderCache)
        .where(
            ProviderCache.cache_key == cache_key,
            ProviderCache.ok.is_(True),
            ProviderCache.fetched_at >= cutoff,
        )
        .order_by(ProviderCache.fetched_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row


def get_last_known_good(
    session: Session,
    provider_name: str,
    endpoint_family: str,
    cache_key: str | None = None,
) -> Optional[ProviderCache]:
    """Newest successful response for this provider, regardless of age.

    When ``cache_key`` is given the match is scoped to that exact request, so a per-race provider
    (candidates, per-race polls) can never fall back onto a *different* race's stored response.
    Without it, falls back to the newest ``ok`` row for the whole ``endpoint_family`` (correct for
    singleton providers such as the generic ballot or presidential history).
    """
    conds = [ProviderCache.provider_name == provider_name, ProviderCache.ok.is_(True)]
    if cache_key is not None:
        conds.append(ProviderCache.cache_key == cache_key)
    else:
        conds.append(ProviderCache.endpoint_family == endpoint_family)
    return session.execute(
        select(ProviderCache).where(*conds).order_by(ProviderCache.fetched_at.desc()).limit(1)
    ).scalar_one_or_none()


def put(
    session: Session,
    *,
    cache_key: str,
    provider_name: str,
    endpoint_family: str,
    url: str,
    params: dict | None,
    ok: bool,
    status_code: int | None,
    payload: Any,
    error: str | None = None,
) -> ProviderCache:
    row = ProviderCache(
        cache_key=cache_key,
        provider_name=provider_name,
        endpoint_family=endpoint_family,
        url=url,
        request_params_json=json.dumps(params or {}, sort_keys=True, default=str),
        status_code=status_code,
        ok=ok,
        response_json=None if payload is None else json.dumps(payload, default=str),
        content_hash=None if payload is None else content_hash(payload),
        error_message=error,
        fetched_at=_utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def decode_payload(row: ProviderCache) -> Any:
    if not row.response_json:
        return None
    return json.loads(row.response_json)
