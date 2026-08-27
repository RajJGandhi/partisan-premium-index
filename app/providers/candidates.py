"""Candidate identity / incumbency providers (spec section 8).

Federal (Senate) races: OpenFEC. Governor races or missing metadata: a seed provider now, an
automated web-search provider later (spec sections 8, 45). Output feeds ``race_candidates`` and
point-in-time ``candidate_status_snapshots``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import get_settings
from app.providers.base import BaseProvider, ProviderChain, ProviderError
from app.providers.normalize import normalize_name

CANDIDATE_KIND = "candidate"


@dataclass
class CandidateRecord:
    race_id: str
    name: str
    normalized_name: str
    party: str  # DEM / REP / OTHER
    is_incumbent: bool
    candidate_status: str  # confirmed / presumptive / unconfirmed / withdrawn
    fec_candidate_id: Optional[str] = None
    committee_ids: list[str] = field(default_factory=list)
    provider: str = "seed"
    source_url: Optional[str] = None


_PARTY_MAP = {
    "DEM": "DEM", "DEMOCRAT": "DEM", "DEMOCRATIC": "DEM", "D": "DEM", "DFL": "DEM",
    "REP": "REP", "REPUBLICAN": "REP", "R": "REP", "GOP": "REP",
}


def _party(value: Any) -> str:
    return _PARTY_MAP.get(str(value or "").strip().upper(), "OTHER")


class OpenFecCandidateProvider(BaseProvider):
    """Federal candidates for a Senate race via OpenFEC ``/candidates/search/``. Disabled without a key."""

    name = "openfec_candidates"
    kind = CANDIDATE_KIND
    endpoint_family = "openfec:candidates_search"

    def __init__(self, **kw):
        super().__init__(**kw)
        s = get_settings()
        self.api_key = s.fec_api_key
        self.base_url = "https://api.open.fec.gov/v1"

    def enabled(self) -> bool:
        return bool(self.api_key)

    def _cache_params(self, *, race_id: str = "", state: str = "", cycle: int = 0, **kwargs) -> dict:
        return {"race_id": race_id, "state": state, "cycle": cycle}

    def _do_fetch(self, *, race_id: str, state: str, cycle: int, office: str = "senate", **kwargs):  # type: ignore[override]
        if office != "senate":
            raise ProviderError(f"{self.name}: only federal (Senate) races supported; got {office}")
        url = f"{self.base_url}/candidates/search/"
        payload, status = self._http_get_json(
            url,
            params={
                "api_key": self.api_key,
                "state": state.upper()[:2],
                "office": "S",
                "election_year": cycle,
                "cycle": cycle,
                "sort": "-first_file_date",
                "per_page": 50,
            },
        )
        if not isinstance(payload, dict) or "results" not in payload:
            raise ProviderError(f"{self.name}: unexpected OpenFEC response")
        return {"race_id": race_id, "results": payload["results"]}, url, status

    def _normalize(self, raw: Any, *, race_id: str = "", **kwargs) -> list[CandidateRecord]:
        rid = (raw or {}).get("race_id", race_id) if isinstance(raw, dict) else race_id
        results = (raw or {}).get("results", []) if isinstance(raw, dict) else []
        out: list[CandidateRecord] = []
        for c in results:
            if not isinstance(c, dict):
                continue
            party = _party(c.get("party") or c.get("party_full"))
            if party == "OTHER":
                continue
            incumbent = str(c.get("incumbent_challenge") or "").upper() == "I" or bool(c.get("incumbent"))
            active = bool(c.get("has_raised_funds", True)) and c.get("candidate_status", "C") in {"C", "N", None}
            out.append(
                CandidateRecord(
                    race_id=rid,
                    name=str(c.get("name") or "").strip(),
                    normalized_name=normalize_name(c.get("name")),
                    party=party,
                    is_incumbent=incumbent,
                    candidate_status="presumptive" if active else "unconfirmed",
                    fec_candidate_id=c.get("candidate_id"),
                    committee_ids=list(c.get("principal_committees_ids", []) or []),
                    provider=self.name,
                    source_url=f"https://www.fec.gov/data/candidate/{c.get('candidate_id')}/",
                )
            )
        # keep only the most plausible D and R nominee (highest is usually first by first_file_date desc)
        best: dict[str, CandidateRecord] = {}
        for rec in out:
            best.setdefault(rec.party, rec)
        return list(best.values())


class SeedCandidateProvider(BaseProvider):
    """Reads candidate metadata already attached to the race config (seed / prior ingest)."""

    name = "seed_candidates"
    kind = CANDIDATE_KIND
    endpoint_family = "seed:candidates"

    def __init__(self, race_config: dict | None = None, **kw):
        super().__init__(**kw)
        self._config = race_config or {}

    def enabled(self) -> bool:
        return bool(self._config)

    def _cache_params(self, *, race_id: str = "", **kwargs) -> dict:
        return {"race_id": race_id}

    def _do_fetch(self, *, race_id: str, **kwargs):  # type: ignore[override]
        cfg = self._config.get(race_id) or {}
        if not cfg:
            raise ProviderError(f"{self.name}: no seed candidates for {race_id}")
        return {"race_id": race_id, **cfg}, "seed:candidates", 200

    def _normalize(self, raw: Any, *, race_id: str = "", **kwargs) -> list[CandidateRecord]:
        cfg = raw if isinstance(raw, dict) else {}
        rid = cfg.get("race_id", race_id)
        out: list[CandidateRecord] = []
        for key, default_party in (("dem_candidate", "DEM"), ("rep_candidate", "REP")):
            c = cfg.get(key)
            if not c:
                continue
            if isinstance(c, str):
                c = {"name": c}
            out.append(
                CandidateRecord(
                    race_id=rid,
                    name=str(c.get("name") or "").strip(),
                    normalized_name=normalize_name(c.get("name")),
                    party=_party(c.get("party") or default_party),
                    is_incumbent=bool(c.get("is_incumbent", False)),
                    candidate_status=c.get("status", "confirmed"),
                    provider=self.name,
                    source_url=c.get("source"),
                )
            )
        return out


class WebCandidateProvider(BaseProvider):
    """Governor / missing-metadata fallback via an injected web-search extractor (spec section 45).
    Without one -> EMPTY (never a guessed candidate)."""

    name = "web_search_candidates"
    kind = CANDIDATE_KIND
    endpoint_family = "web:candidates"

    def __init__(self, extractor=None, **kw):
        super().__init__(**kw)
        self._extractor = extractor

    def enabled(self) -> bool:
        return self._extractor is not None

    def _do_fetch(self, *, race_id: str, **kwargs):  # type: ignore[override]
        assert self._extractor is not None
        return {"race_id": race_id, "results": self._extractor(race_id=race_id, **kwargs) or []}, "web_search", 200

    def _normalize(self, raw: Any, *, race_id: str = "", **kwargs) -> list[CandidateRecord]:
        rid = (raw or {}).get("race_id", race_id) if isinstance(raw, dict) else race_id
        out: list[CandidateRecord] = []
        for c in (raw or {}).get("results", []) if isinstance(raw, dict) else []:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            out.append(
                CandidateRecord(
                    race_id=rid,
                    name=str(c["name"]).strip(),
                    normalized_name=normalize_name(c["name"]),
                    party=_party(c.get("party")),
                    is_incumbent=bool(c.get("is_incumbent", False)),
                    candidate_status=c.get("status", "unconfirmed"),
                    provider=self.name,
                    source_url=c.get("source"),
                )
            )
        return out


def default_candidate_chain(*, race_config: dict | None = None, web_extractor=None) -> ProviderChain:
    return ProviderChain(
        CANDIDATE_KIND,
        [
            OpenFecCandidateProvider(),
            SeedCandidateProvider(race_config=race_config),
            WebCandidateProvider(extractor=web_extractor),
        ],
    )
