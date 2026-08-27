"""Generic-congressional-ballot providers (spec section 10).

Fallback order (spec section 6): VoteHub -> Decision Desk HQ national -> PollingSource -> web.
Each emits a list of normalized generic-ballot polls (``dem_pct`` / ``rep_pct`` + metadata). No
prediction-market data is ever consulted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

from app.config import get_settings
from app.providers.base import BaseProvider, ProviderChain, ProviderError
from app.providers.normalize import (
    bucket_pollster_grade,
    detect_internal,
    detect_partisan_sponsor,
    normalize_population,
)

GENERIC_BALLOT_KIND = "generic_ballot"


def _parse_date(v: Any) -> Optional[date]:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _num(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> Optional[int]:
    # VoteHub returns sample_size as a string, sometimes thousands-separated ("1,024").
    if isinstance(v, str):
        v = v.replace(",", "").replace("%", "").strip()
    try:
        return None if v in (None, "") else int(float(v))
    except (TypeError, ValueError):
        return None


@dataclass
class NormalizedGenericBallotPoll:
    pollster: str
    end_date: Optional[date]
    start_date: Optional[date]
    sample_size: Optional[int]
    population: Optional[str]
    pollster_grade: Optional[str]
    sponsor: Optional[str]
    dem_pct: float
    rep_pct: float
    source_url: Optional[str]
    provider: str
    provider_poll_id: Optional[str]

    @property
    def margin_dem(self) -> float:
        return self.dem_pct - self.rep_pct


def _latest_ts(polls: list[NormalizedGenericBallotPoll]) -> datetime | None:
    ds = [p.end_date for p in polls if p.end_date]
    return datetime.combine(max(ds), datetime.min.time(), tzinfo=timezone.utc) if ds else None


_PARTISAN_WORD = {"REP": "Republican-aligned", "R": "Republican-aligned",
                  "DEM": "Democratic-aligned", "D": "Democratic-aligned"}


def _sponsor_str(row: dict) -> Optional[str]:
    """VoteHub carries sponsor info as a ``sponsors`` array plus ``internal`` (bool) and
    ``partisan`` (nullable "REP"/"DEM"). Fold them into one string the ``normalize`` detectors
    can read (they key off words like "republican"/"internal")."""
    parts: list[str] = []
    sponsors = row.get("sponsors")
    if isinstance(sponsors, list):
        parts.extend(str(s) for s in sponsors if s)
    elif isinstance(sponsors, str) and sponsors.strip():
        parts.append(sponsors.strip())
    if row.get("sponsor"):
        parts.append(str(row["sponsor"]))
    partisan = str(row.get("partisan") or "").strip().upper()
    if partisan:
        parts.append(_PARTISAN_WORD.get(partisan, f"{partisan} partisan"))
    if row.get("internal") is True:
        parts.append("internal poll")
    return " / ".join(dict.fromkeys(parts)) or None


class VoteHubGenericBallotProvider(BaseProvider):
    """VoteHub polls API (``https://api.votehub.com/polls``, public, CC-BY-4.0). Filters to
    ``poll_type=generic-ballot`` for the current cycle; ``answers`` is ``[{choice, pct}]``."""

    name = "votehub_generic_ballot"
    kind = GENERIC_BALLOT_KIND
    endpoint_family = "votehub:generic_ballot"

    def __init__(self, cycle: int = 2026, **kw):
        super().__init__(**kw)
        self.cycle = cycle
        s = get_settings()
        self.base_url = s.votehub_api_base_url.rstrip("/")
        self.api_key = s.votehub_api_key

    def enabled(self) -> bool:
        return bool(self.base_url)

    def _cache_params(self, **kwargs) -> dict:
        return {"cycle": self.cycle}

    def _do_fetch(self, **kwargs) -> tuple[Any, str | None, int | None]:
        url = f"{self.base_url}/polls"
        # No key required today; send it only if the operator configured one.
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        payload, status = self._http_get_json(
            url,
            params={
                "poll_type": "generic-ballot",
                # keep the pull to the current cycle: polls finished on/after the prior Jan 1
                "from_date": date(self.cycle - 1, 1, 1).isoformat(),
            },
            headers=headers,
        )
        rows = payload.get("polls", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ProviderError(f"{self.name}: unexpected shape")
        return rows, url, status

    def _normalize(self, raw: Any, **kwargs) -> list[NormalizedGenericBallotPoll]:
        out: list[NormalizedGenericBallotPoll] = []
        for row in raw if isinstance(raw, list) else []:
            if not isinstance(row, dict):
                continue
            dem = rep = None
            for a in row.get("answers", row.get("choices", [])) or []:
                if not isinstance(a, dict):
                    continue
                label = str(a.get("choice") or a.get("party") or a.get("candidate") or a.get("name") or "").lower()
                val = _num(a.get("pct") or a.get("value"))
                if val is None:
                    continue
                if label.startswith("d") or "democrat" in label:
                    dem = val
                elif label.startswith("r") or "republican" in label:
                    rep = val
            if dem is None or rep is None:
                dem = _num(row.get("dem")) if dem is None else dem
                rep = _num(row.get("rep")) if rep is None else rep
            if dem is None or rep is None:
                continue
            out.append(
                NormalizedGenericBallotPoll(
                    pollster=str(row.get("pollster") or row.get("pollster_name") or "Unknown").strip(),
                    end_date=_parse_date(row.get("end_date") or row.get("endDate")),
                    start_date=_parse_date(row.get("start_date") or row.get("startDate")),
                    sample_size=_int(row.get("sample_size") or row.get("sampleSize")),
                    population=normalize_population(row.get("population") or row.get("population_type")),
                    pollster_grade=bucket_pollster_grade(row.get("pollster_grade") or row.get("grade")),
                    sponsor=_sponsor_str(row),
                    dem_pct=float(dem),
                    rep_pct=float(rep),
                    source_url=row.get("url") or row.get("source"),
                    provider=self.name,
                    provider_poll_id=str(row.get("id") or row.get("poll_id") or "") or None,
                )
            )
        return out

    def _latest_data_timestamp(self, normalized):
        return _latest_ts(normalized)


class DecisionDeskHqGenericBallotProvider(BaseProvider):
    name = "decisiondesk_generic_ballot"
    kind = GENERIC_BALLOT_KIND
    endpoint_family = "ddhq:generic_ballot"

    def __init__(self, cycle: int = 2026, **kw):
        super().__init__(**kw)
        self.cycle = cycle
        self.base_url = get_settings().decisiondesk_polling_base_url.rstrip("/")

    def enabled(self) -> bool:
        return bool(self.base_url)

    def _cache_params(self, **kwargs) -> dict:
        return {"cycle": self.cycle}

    def _do_fetch(self, **kwargs) -> tuple[Any, str | None, int | None]:
        url = f"{self.base_url}/api/v1/polls/generic_ballot"
        payload, status = self._http_get_json(url, params={"cycle": str(self.cycle)})
        if not isinstance(payload, list):
            raise ProviderError(f"{self.name}: expected a JSON array")
        return payload, url, status

    def _normalize(self, raw: Any, **kwargs) -> list[NormalizedGenericBallotPoll]:
        out: list[NormalizedGenericBallotPoll] = []
        for row in raw if isinstance(raw, list) else []:
            if not isinstance(row, dict):
                continue
            if row.get("cycle") is not None and str(row.get("cycle")) != str(self.cycle):
                continue
            dem, rep = _num(row.get("dem_pct")), _num(row.get("rep_pct"))
            if dem is None or rep is None:
                continue
            out.append(
                NormalizedGenericBallotPoll(
                    pollster=str(row.get("pollster") or "Unknown").strip(),
                    end_date=_parse_date(row.get("end_date")),
                    start_date=_parse_date(row.get("start_date")),
                    sample_size=_int(row.get("sample_size")),
                    population=normalize_population(row.get("population")),
                    pollster_grade=bucket_pollster_grade(row.get("pollster_grade")),
                    sponsor=row.get("sponsor"),
                    dem_pct=float(dem),
                    rep_pct=float(rep),
                    source_url=row.get("source"),
                    provider=self.name,
                    provider_poll_id=str(row.get("poll_id")) if row.get("poll_id") is not None else None,
                )
            )
        return out

    def _latest_data_timestamp(self, normalized):
        return _latest_ts(normalized)


class PollingSourceGenericBallotProvider(BaseProvider):
    name = "pollingsource_generic_ballot"
    kind = GENERIC_BALLOT_KIND
    endpoint_family = "pollingsource:generic_ballot"

    def __init__(self, cycle: int = 2026, **kw):
        super().__init__(**kw)
        self.cycle = cycle
        s = get_settings()
        self.base_url = s.pollingsource_api_base_url.rstrip("/")
        self.api_key = s.pollingsource_api_key

    def enabled(self) -> bool:
        return bool(self.base_url)

    def _cache_params(self, **kwargs) -> dict:
        return {"cycle": self.cycle}

    def _do_fetch(self, **kwargs) -> tuple[Any, str | None, int | None]:
        url = f"{self.base_url}/generic-ballot"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        payload, status = self._http_get_json(url, params={"cycle": str(self.cycle)}, headers=headers)
        rows = payload.get("polls", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ProviderError(f"{self.name}: unexpected shape")
        return rows, url, status

    def _normalize(self, raw: Any, **kwargs) -> list[NormalizedGenericBallotPoll]:
        out: list[NormalizedGenericBallotPoll] = []
        for row in raw if isinstance(raw, list) else []:
            if not isinstance(row, dict):
                continue
            dem, rep = _num(row.get("dem_pct") or row.get("dem")), _num(row.get("rep_pct") or row.get("rep"))
            if dem is None or rep is None:
                continue
            out.append(
                NormalizedGenericBallotPoll(
                    pollster=str(row.get("pollster") or "Unknown").strip(),
                    end_date=_parse_date(row.get("end_date")),
                    start_date=_parse_date(row.get("start_date")),
                    sample_size=_int(row.get("sample_size")),
                    population=normalize_population(row.get("population")),
                    pollster_grade=bucket_pollster_grade(row.get("grade")),
                    sponsor=row.get("sponsor"),
                    dem_pct=float(dem),
                    rep_pct=float(rep),
                    source_url=row.get("source"),
                    provider=self.name,
                    provider_poll_id=str(row.get("id") or "") or None,
                )
            )
        return out

    def _latest_data_timestamp(self, normalized):
        return _latest_ts(normalized)


class WebSearchGenericBallotProvider(BaseProvider):
    name = "web_search_generic_ballot"
    kind = GENERIC_BALLOT_KIND
    endpoint_family = "web:generic_ballot"

    def __init__(self, extractor: Optional[Callable[..., list[dict]]] = None, **kw):
        super().__init__(**kw)
        self._extractor = extractor

    def enabled(self) -> bool:
        return self._extractor is not None

    def _do_fetch(self, **kwargs) -> tuple[Any, str | None, int | None]:
        assert self._extractor is not None
        return (self._extractor(**kwargs) or []), "web_search", 200

    def _normalize(self, raw: Any, **kwargs) -> list[NormalizedGenericBallotPoll]:
        out: list[NormalizedGenericBallotPoll] = []
        for row in raw if isinstance(raw, list) else []:
            if not isinstance(row, dict):
                continue
            dem, rep = _num(row.get("dem_pct")), _num(row.get("rep_pct"))
            if dem is None or rep is None:
                continue
            out.append(
                NormalizedGenericBallotPoll(
                    pollster=str(row.get("pollster") or "Unknown"),
                    end_date=_parse_date(row.get("end_date")),
                    start_date=_parse_date(row.get("start_date")),
                    sample_size=_int(row.get("sample_size")),
                    population=normalize_population(row.get("population")),
                    pollster_grade=bucket_pollster_grade(row.get("pollster_grade")),
                    sponsor=row.get("sponsor"),
                    dem_pct=float(dem),
                    rep_pct=float(rep),
                    source_url=row.get("source"),
                    provider=self.name,
                    provider_poll_id=None,
                )
            )
        return out


def generic_ballot_content_hash(p: NormalizedGenericBallotPoll) -> str:
    import hashlib

    from app.providers.normalize import normalize_name

    material = "|".join(
        [
            normalize_name(p.pollster),
            p.start_date.isoformat() if p.start_date else "",
            p.end_date.isoformat() if p.end_date else "",
            str(p.sample_size or ""),
            (p.population or "").upper(),
            f"{p.dem_pct:.1f}",
            f"{p.rep_pct:.1f}",
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def default_generic_ballot_chain(cycle: int = 2026, *, web_extractor=None) -> ProviderChain:
    return ProviderChain(
        GENERIC_BALLOT_KIND,
        [
            VoteHubGenericBallotProvider(cycle=cycle),
            DecisionDeskHqGenericBallotProvider(cycle=cycle),
            PollingSourceGenericBallotProvider(cycle=cycle),
            WebSearchGenericBallotProvider(extractor=web_extractor),
        ],
    )


def partisan_fields(p: NormalizedGenericBallotPoll) -> tuple[Optional[str], bool]:
    return detect_partisan_sponsor(p.sponsor), detect_internal(p.sponsor)
