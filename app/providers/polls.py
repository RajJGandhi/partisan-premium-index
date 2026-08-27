"""Race-poll providers (spec section 6).

Primary: Decision Desk HQ Polling API ``/api/v1/polls/ballot_test`` (one row per candidate; grouped
here into head-to-head polls). Secondary/verification: a generic PollingSource JSON adapter.
Fallback: an injectable web-search extractor (disabled -> EMPTY, never an error).

Providers emit **raw normalized polls** (a list of head-to-head dicts). Race attachment + party
resolution + de-dup happen in :func:`polls_to_observations`, which the ingest orchestrator calls
with the set of known races.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Optional

from app.config import get_settings
from app.providers.base import BaseProvider, ProviderError
from app.providers.normalize import (
    bucket_pollster_grade,
    canonical_office,
    detect_internal,
    detect_partisan_sponsor,
    normalize_population,
    poll_content_hash,
    state_to_abbr,
)
from app.providers.race_identity import KnownRace, match_to_race, resolve_candidate_party

POLL_KIND = "poll"


def _parse_date(v: Any) -> Optional[date]:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


@dataclass
class NormalizedRacePoll:
    pollster: str
    end_date: Optional[date]
    start_date: Optional[date]
    sample_size: Optional[int]
    population: Optional[str]
    pollster_grade: Optional[str]
    sponsor: Optional[str]
    state: str
    office: str  # senate / governor
    cycle: int
    election_type: Optional[str]
    answers: list[dict]  # [{"name": str, "pct": float}]
    source_url: Optional[str]
    provider: str
    provider_poll_id: Optional[str]
    senate_class: Optional[str] = None
    district: Optional[int] = None


# --------------------------------------------------------------------------------------------------
class DecisionDeskHqPollProvider(BaseProvider):
    """DDHQ ballot-test (trial-heat) polls for Senate + Governor general elections."""

    name = "decisiondesk_ballot_test"
    kind = POLL_KIND
    endpoint_family = "ddhq:ballot_test"

    def __init__(self, cycle: int = 2026, **kw):
        super().__init__(**kw)
        self.cycle = cycle
        self.base_url = get_settings().decisiondesk_polling_base_url.rstrip("/")

    def enabled(self) -> bool:
        return bool(self.base_url)

    def _cache_params(self, **kwargs) -> dict:
        return {"cycle": self.cycle}

    def _do_fetch(self, **kwargs) -> tuple[Any, str | None, int | None]:
        url = f"{self.base_url}/api/v1/polls/ballot_test"
        payload, status = self._http_get_json(url, params={"cycle": str(self.cycle)})
        if not isinstance(payload, list):
            raise ProviderError(f"{self.name}: expected a JSON array, got {type(payload).__name__}")
        return payload, url, status

    def _normalize(self, raw: Any, **kwargs) -> list[NormalizedRacePoll]:
        if not isinstance(raw, list):
            return []
        groups: dict[tuple, list[dict]] = defaultdict(list)
        meta: dict[tuple, dict] = {}
        for row in raw:
            if not isinstance(row, dict):
                continue
            if str(row.get("cycle")) not in {str(self.cycle), "None", ""}:
                if row.get("cycle") is not None and str(row.get("cycle")) != str(self.cycle):
                    continue
            office = canonical_office(row.get("office_type") or "")
            if office not in {"senate", "governor"}:
                continue
            if (row.get("election_type") or "General").lower() == "primary":
                continue
            key = (row.get("poll_id"), row.get("question_id"))
            groups[key].append(row)
            meta.setdefault(key, row)

        out: list[NormalizedRacePoll] = []
        for key, rows in groups.items():
            m = meta[key]
            answers = [
                {"name": str(r.get("candidate_name") or "").strip(), "pct": _num(r.get("pct"))}
                for r in rows
                if r.get("candidate_name") and r.get("pct") is not None
            ]
            if len(answers) < 2:
                continue
            sponsor = m.get("sponsor")
            out.append(
                NormalizedRacePoll(
                    pollster=str(m.get("pollster") or "Unknown").strip(),
                    end_date=_parse_date(m.get("end_date")),
                    start_date=_parse_date(m.get("start_date")),
                    sample_size=_int(m.get("sample_size")),
                    population=normalize_population(m.get("population")),
                    pollster_grade=bucket_pollster_grade(m.get("pollster_grade") or m.get("grade")),
                    sponsor=sponsor,
                    state=str(m.get("state") or "").strip().upper()[:2],
                    office=canonical_office(m.get("office_type") or ""),
                    cycle=int(str(m.get("cycle") or self.cycle)[:4]),
                    election_type=m.get("election_type"),
                    answers=answers,
                    source_url=m.get("source"),
                    provider=self.name,
                    provider_poll_id=str(m.get("poll_id")) if m.get("poll_id") is not None else None,
                    senate_class=str(m.get("senate_class")) if m.get("senate_class") is not None else None,
                    district=_int(m.get("district")),
                )
            )
        return out

    def _latest_data_timestamp(self, normalized: list[NormalizedRacePoll]):
        dates = [p.end_date for p in normalized if p.end_date]
        if not dates:
            return None
        from datetime import datetime, timezone

        return datetime.combine(max(dates), datetime.min.time(), tzinfo=timezone.utc)


class VoteHubRacePollProvider(BaseProvider):
    """VoteHub state Senate + Governor trial-heat polls (``https://api.votehub.com/polls``,
    public, CC-BY-4.0). One request per ``poll_type`` (``us-senator`` / ``governor``); the
    state comes from the ``subject`` string ("2026 North Carolina"). Primary polls -- subject
    ending " Democratic" / " Republican" / " Primary" -- are dropped so only general-election
    head-to-heads reach :func:`polls_to_observations`."""

    name = "votehub_race_polls"
    kind = POLL_KIND
    endpoint_family = "votehub:race_polls"

    _POLL_TYPES = {"us-senator": "senate", "governor": "governor"}

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
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        rows: list[dict] = []
        for poll_type in self._POLL_TYPES:
            payload, _status = self._http_get_json(
                url,
                params={"poll_type": poll_type, "from_date": f"{self.cycle - 1}-01-01"},
                headers=headers,
            )
            got = payload.get("polls", payload) if isinstance(payload, dict) else payload
            for r in got if isinstance(got, list) else []:
                if isinstance(r, dict):
                    r.setdefault("poll_type", poll_type)
                    rows.append(r)
        if not rows:
            raise ProviderError(f"{self.name}: no Senate/Governor polls returned")
        return rows, url, 200

    @staticmethod
    def _state_from_subject(subject: str) -> tuple[Optional[str], bool]:
        """('NC', is_primary) from a VoteHub subject like '2026 North Carolina' /
        '2026 Texas Democratic'."""
        text = re.sub(r"^\s*\d{4}\s+", "", str(subject or "")).strip()
        is_primary = False
        m = re.search(r"\s+(Democratic|Republican|Primary)\s*$", text, re.IGNORECASE)
        if m:
            is_primary = True
            text = text[: m.start()].strip()
        return state_to_abbr(text), is_primary

    def _normalize(self, raw: Any, **kwargs) -> list[NormalizedRacePoll]:
        out: list[NormalizedRacePoll] = []
        for row in raw if isinstance(raw, list) else []:
            if not isinstance(row, dict):
                continue
            office = self._POLL_TYPES.get(str(row.get("poll_type") or ""))
            if office is None:
                continue
            state, is_primary = self._state_from_subject(row.get("subject", ""))
            if not state or is_primary:
                continue
            answers = [
                {"name": str(a.get("choice") or a.get("name") or "").strip(), "pct": _num(a.get("pct"))}
                for a in row.get("answers", []) or []
                if isinstance(a, dict)
            ]
            answers = [a for a in answers if a["name"] and a["pct"] is not None]
            if len(answers) < 2:
                continue
            out.append(
                NormalizedRacePoll(
                    pollster=str(row.get("pollster") or "Unknown").strip(),
                    end_date=_parse_date(row.get("end_date")),
                    start_date=_parse_date(row.get("start_date")),
                    sample_size=_int(row.get("sample_size")),
                    population=normalize_population(row.get("population")),
                    pollster_grade=bucket_pollster_grade(row.get("pollster_grade") or row.get("grade")),
                    sponsor=_votehub_sponsor(row),
                    state=state,
                    office=office,
                    cycle=self.cycle,
                    election_type="General",
                    answers=answers,
                    source_url=row.get("url") or row.get("source"),
                    provider=self.name,
                    provider_poll_id=str(row.get("id") or "") or None,
                )
            )
        return out

    def _latest_data_timestamp(self, normalized: list[NormalizedRacePoll]):
        dates = [p.end_date for p in normalized if p.end_date]
        if not dates:
            return None
        from datetime import datetime, timezone

        return datetime.combine(max(dates), datetime.min.time(), tzinfo=timezone.utc)


_PARTISAN_WORD = {"REP": "Republican-aligned", "DEM": "Democratic-aligned"}


def _votehub_sponsor(row: dict) -> Optional[str]:
    parts: list[str] = []
    sponsors = row.get("sponsors")
    if isinstance(sponsors, list):
        parts.extend(str(s) for s in sponsors if s)
    elif isinstance(sponsors, str) and sponsors.strip():
        parts.append(sponsors.strip())
    partisan = str(row.get("partisan") or "").strip().upper()
    if partisan:
        parts.append(_PARTISAN_WORD.get(partisan, f"{partisan} partisan"))
    if row.get("internal") is True:
        parts.append("internal poll")
    return " / ".join(dict.fromkeys(parts)) or None


class PollingSourcePollProvider(BaseProvider):
    """Generic secondary provider: expects a JSON array of head-to-head poll objects at
    ``{POLLINGSOURCE_API_BASE_URL}/polls?cycle=<cycle>&type=general``. Disabled if unconfigured."""

    name = "pollingsource_polls"
    kind = POLL_KIND
    endpoint_family = "pollingsource:polls"

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
        url = f"{self.base_url}/polls"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        payload, status = self._http_get_json(
            url, params={"cycle": str(self.cycle), "type": "general"}, headers=headers
        )
        rows = payload.get("polls", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ProviderError(f"{self.name}: unexpected response shape")
        return rows, url, status

    def _normalize(self, raw: Any, **kwargs) -> list[NormalizedRacePoll]:
        out: list[NormalizedRacePoll] = []
        for row in raw if isinstance(raw, list) else []:
            if not isinstance(row, dict):
                continue
            office = canonical_office(row.get("office") or row.get("office_type") or "")
            if office not in {"senate", "governor"}:
                continue
            answers = row.get("answers") or row.get("candidates") or []
            norm_answers = [
                {"name": str(a.get("name") or a.get("candidate") or "").strip(), "pct": _num(a.get("pct") or a.get("value"))}
                for a in answers
                if isinstance(a, dict)
            ]
            norm_answers = [a for a in norm_answers if a["name"] and a["pct"] is not None]
            if len(norm_answers) < 2:
                # some feeds give dem_pct/rep_pct inline
                if row.get("dem_pct") is not None and row.get("rep_pct") is not None:
                    norm_answers = [
                        {"name": str(row.get("dem_candidate") or "Democrat"), "pct": _num(row.get("dem_pct"))},
                        {"name": str(row.get("rep_candidate") or "Republican"), "pct": _num(row.get("rep_pct"))},
                    ]
                else:
                    continue
            out.append(
                NormalizedRacePoll(
                    pollster=str(row.get("pollster") or row.get("pollster_name") or "Unknown").strip(),
                    end_date=_parse_date(row.get("end_date") or row.get("endDate")),
                    start_date=_parse_date(row.get("start_date") or row.get("startDate")),
                    sample_size=_int(row.get("sample_size") or row.get("sampleSize")),
                    population=normalize_population(row.get("population") or row.get("population_type")),
                    pollster_grade=bucket_pollster_grade(row.get("pollster_grade") or row.get("grade")),
                    sponsor=row.get("sponsor") or row.get("partisan"),
                    state=str(row.get("state") or "").strip().upper()[:2],
                    office=office,
                    cycle=int(str(row.get("cycle") or self.cycle)[:4]),
                    election_type=row.get("election_type") or "General",
                    answers=norm_answers,
                    source_url=row.get("source") or row.get("url"),
                    provider=self.name,
                    provider_poll_id=str(row.get("id") or row.get("poll_id") or "") or None,
                )
            )
        return out


class WebSearchPollProvider(BaseProvider):
    """Last-resort: a web-search extractor supplied as a callable (spec section 45). Without one,
    yields EMPTY -- never an error, never a fabricated poll."""

    name = "web_search_polls"
    kind = POLL_KIND
    endpoint_family = "web:polls"

    def __init__(self, extractor: Optional[Callable[..., list[dict]]] = None, **kw):
        super().__init__(**kw)
        self._extractor = extractor

    def enabled(self) -> bool:
        return self._extractor is not None

    def _do_fetch(self, **kwargs) -> tuple[Any, str | None, int | None]:
        assert self._extractor is not None
        rows = self._extractor(**kwargs)
        return (rows or []), "web_search", 200

    def _normalize(self, raw: Any, **kwargs) -> list[NormalizedRacePoll]:
        out: list[NormalizedRacePoll] = []
        for row in raw if isinstance(raw, list) else []:
            if not isinstance(row, dict) or not row.get("answers"):
                continue
            out.append(
                NormalizedRacePoll(
                    pollster=str(row.get("pollster") or "Unknown"),
                    end_date=_parse_date(row.get("end_date")),
                    start_date=_parse_date(row.get("start_date")),
                    sample_size=_int(row.get("sample_size")),
                    population=normalize_population(row.get("population")),
                    pollster_grade=bucket_pollster_grade(row.get("pollster_grade")),
                    sponsor=row.get("sponsor"),
                    state=str(row.get("state") or "").upper()[:2],
                    office=canonical_office(row.get("office") or ""),
                    cycle=int(row.get("cycle") or 0) or 2026,
                    election_type="General",
                    answers=[
                        {"name": str(a.get("name") or ""), "pct": _num(a.get("pct"))}
                        for a in row["answers"]
                        if isinstance(a, dict)
                    ],
                    source_url=row.get("source"),
                    provider=self.name,
                    provider_poll_id=None,
                )
            )
        return out


# --------------------------------------------------------------------------------------------------
@dataclass
class PollObservationRow:
    """Flat shape ready to persist to ``poll_observations`` and to feed the Quant engine."""

    race_id: str
    provider: str
    provider_poll_id: Optional[str]
    pollster: str
    start_date: Optional[date]
    end_date: Optional[date]
    sample_size: Optional[int]
    population: Optional[str]
    pollster_grade: Optional[str]
    partisan_sponsor: Optional[str]
    internal: bool
    dem_candidate: Optional[str]
    rep_candidate: Optional[str]
    dem_pct: float
    rep_pct: float
    margin_dem: float
    source_url: Optional[str]
    content_hash: str
    match_confidence: float
    match_method: str


def polls_to_observations(
    polls: list[NormalizedRacePoll],
    known_races: list[KnownRace],
    *,
    llm_resolver=None,
) -> tuple[list[PollObservationRow], list[dict]]:
    """Attach each raw poll to a race, resolve D/R, de-dup. Returns ``(rows, skipped)``.

    ``skipped`` entries record *why* a poll was dropped (unmatched race, party unresolved,
    duplicate) so nothing disappears silently.
    """
    rows: list[PollObservationRow] = []
    skipped: list[dict] = []
    seen: set[str] = set()

    for p in polls:
        names = [a["name"] for a in p.answers if a["name"]]
        match = match_to_race(
            state=p.state, office=p.office, cycle=p.cycle,
            candidate_names=names, known_races=known_races, llm_resolver=llm_resolver,
        )
        if not match.matched or match.race_id is None:
            skipped.append({"reason": "unmatched_race", "detail": match.rationale, "pollster": p.pollster, "state": p.state})
            continue
        matched_race_id: str = match.race_id
        race = next((r for r in known_races if r.race_id == matched_race_id), None)

        # resolve which answer is Dem / Rep
        dem_ans = rep_ans = None
        for a in p.answers:
            party = resolve_candidate_party(
                a["name"],
                dem_candidate=race.dem_candidate if race else None,
                rep_candidate=race.rep_candidate if race else None,
            )
            if party == "DEM" and dem_ans is None:
                dem_ans = a
            elif party == "REP" and rep_ans is None:
                rep_ans = a
        if (dem_ans is None or rep_ans is None) and len(p.answers) >= 2:
            # fall back to the two highest-scoring answers, ordered by known-name hint if any
            top2 = sorted(p.answers, key=lambda a: a["pct"] or 0, reverse=True)[:2]
            if dem_ans is None and rep_ans is None:
                skipped.append({"reason": "party_unresolved", "pollster": p.pollster, "race_id": matched_race_id,
                                "answers": names})
                continue
            other = next((a for a in top2 if a not in (dem_ans, rep_ans)), None)
            if dem_ans is None:
                dem_ans = other
            elif rep_ans is None:
                rep_ans = other
        if dem_ans is None or rep_ans is None:
            skipped.append({"reason": "party_unresolved", "pollster": p.pollster, "race_id": matched_race_id})
            continue

        dem_pct, rep_pct = float(dem_ans["pct"]), float(rep_ans["pct"])
        ch = poll_content_hash(
            pollster=p.pollster, start_date=p.start_date, end_date=p.end_date,
            sample_size=p.sample_size, population=p.population, dem_pct=dem_pct, rep_pct=rep_pct,
            dem_candidate=dem_ans["name"], rep_candidate=rep_ans["name"], race_id=matched_race_id,
        )
        if ch in seen:
            skipped.append({"reason": "duplicate_release", "pollster": p.pollster, "race_id": matched_race_id})
            continue
        seen.add(ch)

        rows.append(
            PollObservationRow(
                race_id=matched_race_id,
                provider=p.provider,
                provider_poll_id=p.provider_poll_id,
                pollster=p.pollster,
                start_date=p.start_date,
                end_date=p.end_date,
                sample_size=p.sample_size,
                population=p.population,
                pollster_grade=p.pollster_grade,
                partisan_sponsor=detect_partisan_sponsor(p.sponsor),
                internal=detect_internal(p.sponsor),
                dem_candidate=dem_ans["name"],
                rep_candidate=rep_ans["name"],
                dem_pct=dem_pct,
                rep_pct=rep_pct,
                margin_dem=dem_pct - rep_pct,
                source_url=p.source_url,
                content_hash=ch,
                match_confidence=match.confidence,
                match_method=match.method,
            )
        )
    return rows, skipped


def default_poll_chain(cycle: int = 2026, *, web_extractor=None):
    from app.providers.base import ProviderChain

    # VoteHub first: it is public and currently the only source returning live Senate/Governor
    # trial-heat polls. DDHQ's ballot_test endpoint stays as the verification fallback (it 200s
    # but has been returning an empty array); PollingSource + web are the deeper fallbacks.
    return ProviderChain(
        POLL_KIND,
        [
            VoteHubRacePollProvider(cycle=cycle),
            DecisionDeskHqPollProvider(cycle=cycle),
            PollingSourcePollProvider(cycle=cycle),
            WebSearchPollProvider(extractor=web_extractor),
        ],
    )


def _num(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> Optional[int]:
    try:
        return None if v is None or v == "" else int(float(v))
    except (TypeError, ValueError):
        return None
