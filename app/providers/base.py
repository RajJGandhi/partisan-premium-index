"""Provider abstraction: caching, retry/backoff, validation, last-known-good, health, fallback.

``BaseProvider.fetch`` is the template method every concrete provider inherits (spec sections 5,
31): it wraps a subclass's ``_do_fetch`` + ``_normalize`` with a fresh-cache check, bounded
exponential-backoff retries, response validation, a last-known-good fallback that reports an
explicit ``STALE`` status (never a silent zero), and a ``provider_health`` update.

``ProviderChain`` runs several providers of one kind in a fixed order and records exactly one
``data_provider_runs`` row with ``provider_requested`` / ``provider_used`` / ``fallback_reason``.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models_quant import DataProviderRun, ProviderHealth
from app.providers import cache as _cache


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderError(RuntimeError):
    """A provider failed in a way that should trigger fallback (not a bug)."""


# Terminal statuses a ProviderResult can carry.
OK = "OK"          # fresh, validated, non-empty
EMPTY = "EMPTY"     # provider ran but returned nothing usable (disabled/unconfigured or 0 rows)
STALE = "STALE"     # served from last-known-good after every live attempt failed
FAILED = "FAILED"   # no data and no last-known-good


@dataclass
class ProviderResult:
    """One normalized data acquisition (spec section 5). Carries full provenance."""

    provider: str
    kind: str
    status: str  # OK / EMPTY / STALE / FAILED
    validation_status: str  # OK / EMPTY / INVALID
    retrieved_at: datetime
    source_id: Optional[str] = None
    source_url: Optional[str] = None
    original_dates: dict = field(default_factory=dict)
    raw_payload: Any = None
    normalized_payload: Any = None
    content_hash: Optional[str] = None
    from_cache: bool = False
    from_last_known_good: bool = False
    latency_ms: Optional[int] = None
    retries: int = 0
    error: Optional[str] = None

    @property
    def usable(self) -> bool:
        return self.status in (OK, STALE) and bool(self.normalized_payload)


@dataclass
class ChainResult:
    kind: str
    result: Optional[ProviderResult]
    provider_requested: str
    provider_used: Optional[str]
    fallback_reason: Optional[str]
    attempts: list[ProviderResult] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.result is not None and self.result.usable


# --------------------------------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------------------------------
def record_provider_health(
    session: Session,
    *,
    provider_name: str,
    provider_kind: str,
    ok: bool,
    latency_ms: int | None,
    latest_data_timestamp: datetime | None,
    error: str | None,
    stale: bool = False,
) -> None:
    row = session.execute(
        select(ProviderHealth).where(ProviderHealth.provider_name == provider_name)
    ).scalar_one_or_none()
    if row is None:
        row = ProviderHealth(provider_name=provider_name, provider_kind=provider_kind)
        session.add(row)
    now = _utcnow()
    row.provider_kind = provider_kind
    row.last_attempt_at = now
    row.last_latency_ms = latency_ms
    if ok:
        row.last_success_at = now
        row.consecutive_failures = 0
        row.recent_error = None
        if latest_data_timestamp is not None:
            row.latest_data_timestamp = latest_data_timestamp
        row.is_stale = bool(stale)
        row.status = "DEGRADED" if stale else "HEALTHY"
    else:
        row.consecutive_failures = (row.consecutive_failures or 0) + 1
        row.recent_error = (error or "unknown error")[:2000]
        row.is_stale = True
        row.status = "DOWN" if row.consecutive_failures >= 3 else "DEGRADED"
    session.flush()


# --------------------------------------------------------------------------------------------------
# BaseProvider
# --------------------------------------------------------------------------------------------------
class BaseProvider(ABC):
    """One data source. Subclasses implement ``_do_fetch`` (I/O) and ``_normalize`` (shape)."""

    #: stable, unique provider name (persisted); e.g. "decisiondesk_ballot_test"
    name: str = "base"
    #: provider kind: poll / generic_ballot / election_history / candidate / market_discovery / web_evidence
    kind: str = "base"
    #: coarse endpoint identity for last-known-good grouping; e.g. "ddhq:ballot_test"
    endpoint_family: str = "base"

    def __init__(
        self,
        *,
        timeout: int | None = None,
        max_retries: int | None = None,
        backoff_base_seconds: float | None = None,
    ):
        s = get_settings()
        self.timeout = timeout if timeout is not None else s.provider_timeout_seconds
        self.max_retries = max_retries if max_retries is not None else s.provider_max_retries
        self.cache_ttl_minutes = s.provider_cache_ttl_minutes
        self.user_agent = s.source_user_agent
        # base for exponential backoff between retries; tests pass 0 to avoid real sleeps
        self.backoff_base_seconds = (
            backoff_base_seconds
            if backoff_base_seconds is not None
            else s.provider_retry_backoff_seconds
        )

    # -- subclass hooks ---------------------------------------------------------------------------
    def enabled(self) -> bool:
        """Override to gate on a configured base URL / key. A disabled provider yields EMPTY."""
        return True

    @abstractmethod
    def _do_fetch(self, **kwargs) -> tuple[Any, str | None, int | None]:
        """Return ``(raw_payload, source_url, status_code)``. Raise :class:`ProviderError` to fall back."""

    @abstractmethod
    def _normalize(self, raw: Any, **kwargs) -> Any:
        """Turn the raw payload into the normalized shape this kind produces (list/dict)."""

    def _validate(self, normalized: Any) -> str:
        if normalized is None or (hasattr(normalized, "__len__") and len(normalized) == 0):
            return EMPTY
        return "OK"

    def _latest_data_timestamp(self, normalized: Any) -> datetime | None:  # noqa: ARG002
        return None

    def _cache_params(self, **kwargs) -> dict:
        """Params that make the request unique (for the cache key). Override if kwargs aren't hashable."""
        return {k: v for k, v in kwargs.items() if isinstance(v, (str, int, float, bool, type(None)))}

    # -- HTTP helper ----------------------------------------------------------------------------
    def _http_get_json(self, url: str, params: dict | None = None, headers: dict | None = None) -> tuple[Any, int]:
        hdrs = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=self.timeout)
        except requests.RequestException as exc:  # network / timeout
            raise ProviderError(f"{self.name}: request to {url} failed: {exc}") from exc
        if resp.status_code >= 500:
            raise ProviderError(f"{self.name}: {url} returned {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderError(f"{self.name}: {url} returned {resp.status_code} {resp.text[:200]}")
        try:
            return resp.json(), resp.status_code
        except ValueError as exc:
            raise ProviderError(f"{self.name}: {url} returned non-JSON body") from exc

    # -- template method ---------------------------------------------------------------------
    def fetch(
        self,
        session: Session,
        *,
        allow_cache: bool = True,
        allow_last_known_good: bool = True,
        **kwargs,
    ) -> ProviderResult:
        started = time.monotonic()
        now = _utcnow()

        if not self.enabled():
            return ProviderResult(
                provider=self.name,
                kind=self.kind,
                status=EMPTY,
                validation_status=EMPTY,
                retrieved_at=now,
                error="provider not enabled (missing base URL / key)",
            )

        cache_params = self._cache_params(**kwargs)
        cache_key = _cache.make_cache_key(self.name, "GET", self.endpoint_family, cache_params)

        if allow_cache:
            hit = _cache.get_fresh(session, cache_key, self.cache_ttl_minutes)
            if hit is not None:
                raw = _cache.decode_payload(hit)
                normalized = self._normalize(raw, **kwargs)
                validation = self._validate(normalized)
                return ProviderResult(
                    provider=self.name,
                    kind=self.kind,
                    status=OK if validation == "OK" else EMPTY,
                    validation_status=validation,
                    retrieved_at=hit.fetched_at,
                    source_url=hit.url,
                    raw_payload=raw,
                    normalized_payload=normalized,
                    content_hash=hit.content_hash,
                    from_cache=True,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )

        # live fetch with bounded exponential backoff
        last_exc: Exception | None = None
        retries = 0
        for attempt in range(1, self.max_retries + 1):
            try:
                raw, source_url, status_code = self._do_fetch(**kwargs)
                normalized = self._normalize(raw, **kwargs)
                validation = self._validate(normalized)
                ch = _cache.content_hash(raw)
                _cache.put(
                    session,
                    cache_key=cache_key,
                    provider_name=self.name,
                    endpoint_family=self.endpoint_family,
                    url=source_url or self.endpoint_family,
                    params=cache_params,
                    ok=validation != "INVALID",
                    status_code=status_code,
                    payload=raw,
                )
                latency = int((time.monotonic() - started) * 1000)
                record_provider_health(
                    session,
                    provider_name=self.name,
                    provider_kind=self.kind,
                    ok=validation != "INVALID",
                    latency_ms=latency,
                    latest_data_timestamp=self._latest_data_timestamp(normalized),
                    error=None,
                    stale=False,
                )
                return ProviderResult(
                    provider=self.name,
                    kind=self.kind,
                    status=OK if validation == "OK" else EMPTY,
                    validation_status=validation,
                    retrieved_at=_utcnow(),
                    source_url=source_url,
                    raw_payload=raw,
                    normalized_payload=normalized,
                    content_hash=ch,
                    retries=retries,
                    latency_ms=latency,
                )
            except ProviderError as exc:
                last_exc = exc
                retries = attempt
                if attempt < self.max_retries:
                    delay = min(2 ** (attempt - 1), 8) * self.backoff_base_seconds
                    if delay > 0:
                        time.sleep(delay)
            except Exception as exc:  # unexpected: record and fall back, never crash the pipeline
                last_exc = exc
                retries = attempt
                break

        err = f"{type(last_exc).__name__}: {last_exc}" if last_exc else "unknown failure"
        latency = int((time.monotonic() - started) * 1000)

        if allow_last_known_good:
            lkg = _cache.get_last_known_good(session, self.name, self.endpoint_family)
            if lkg is not None:
                raw = _cache.decode_payload(lkg)
                normalized = self._normalize(raw, **kwargs)
                record_provider_health(
                    session,
                    provider_name=self.name,
                    provider_kind=self.kind,
                    ok=True,
                    latency_ms=latency,
                    latest_data_timestamp=self._latest_data_timestamp(normalized),
                    error=err,
                    stale=True,
                )
                return ProviderResult(
                    provider=self.name,
                    kind=self.kind,
                    status=STALE,
                    validation_status=self._validate(normalized),
                    retrieved_at=lkg.fetched_at,
                    source_url=lkg.url,
                    raw_payload=raw,
                    normalized_payload=normalized,
                    content_hash=lkg.content_hash,
                    from_last_known_good=True,
                    retries=retries,
                    latency_ms=latency,
                    error=err,
                )

        record_provider_health(
            session,
            provider_name=self.name,
            provider_kind=self.kind,
            ok=False,
            latency_ms=latency,
            latest_data_timestamp=None,
            error=err,
        )
        return ProviderResult(
            provider=self.name,
            kind=self.kind,
            status=FAILED,
            validation_status="INVALID",
            retrieved_at=now,
            retries=retries,
            latency_ms=latency,
            error=err,
        )


# --------------------------------------------------------------------------------------------------
# ProviderChain
# --------------------------------------------------------------------------------------------------
class ProviderChain:
    """Ordered fallback across providers of one kind (spec section 6 / 31 fallback ordering)."""

    def __init__(self, kind: str, providers: Sequence[BaseProvider]):
        if not providers:
            raise ValueError("ProviderChain needs at least one provider")
        self.kind = kind
        self.providers = list(providers)

    def run(
        self,
        session: Session,
        *,
        target_ref: str,
        job_run_id: int | None = None,
        allow_cache: bool = True,
        **kwargs,
    ) -> ChainResult:
        requested = self.providers[0].name
        attempts: list[ProviderResult] = []
        fresh_win: ProviderResult | None = None
        stale_win: ProviderResult | None = None
        reasons: list[str] = []

        for provider in self.providers:
            res = provider.fetch(session, allow_cache=allow_cache, **kwargs)
            attempts.append(res)
            if res.status == OK and res.usable:
                fresh_win = res
                break
            if res.status == STALE and res.usable and stale_win is None:
                stale_win = res
            reasons.append(f"{provider.name}={res.status.lower()}" + (f" ({res.error})" if res.error else ""))

        winner = fresh_win or stale_win
        fallback_reason = None
        if winner is not None and winner.provider != requested:
            fallback_reason = "; ".join(reasons[: self.providers.index(next(p for p in self.providers if p.name == winner.provider))])
        elif winner is None:
            fallback_reason = "; ".join(reasons) or "all providers returned no usable data"
        elif winner.from_last_known_good:
            fallback_reason = "; ".join(reasons) + " -> served last-known-good (STALE)"

        started_at = attempts[0].retrieved_at if attempts else _utcnow()
        run_row = DataProviderRun(
            job_run_id=job_run_id,
            provider_kind=self.kind,
            provider_requested=requested,
            provider_used=winner.provider if winner else None,
            fallback_reason=fallback_reason,
            target_ref=target_ref,
            started_at=started_at,
            finished_at=_utcnow(),
            status=(winner.status if winner else FAILED),
            latency_ms=sum(a.latency_ms or 0 for a in attempts),
            retries=sum(a.retries for a in attempts),
            items_ingested=len(winner.normalized_payload) if winner and hasattr(winner.normalized_payload, "__len__") else 0,
            used_cache=any(a.from_cache for a in attempts),
            used_last_known_good=bool(winner and winner.from_last_known_good),
            response_hash=winner.content_hash if winner else None,
            sanitized_error=None if winner else "; ".join(a.error for a in attempts if a.error)[:2000] or None,
        )
        session.add(run_row)
        session.flush()

        return ChainResult(
            kind=self.kind,
            result=winner,
            provider_requested=requested,
            provider_used=winner.provider if winner else None,
            fallback_reason=fallback_reason,
            attempts=attempts,
        )
