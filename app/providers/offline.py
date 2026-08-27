"""Offline / fixture providers.

These read structured polls, generic-ballot polls and candidate metadata from a local races JSON
file (the same shape as ``data/seed/quant_example_races.json``). They let the full
``providers -> DB -> engine`` path run with no network and no keys -- for the offline shadow smoke
run and for tests -- while still exercising the real chain, cache, health and de-dup machinery.

They are a legitimate structured fallback source (spec section 45), tagged ``provider="seed_file"``
so their provenance is never confused with a live API.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

from app.providers.base import BaseProvider, ProviderChain, ProviderError
from app.providers.candidates import CANDIDATE_KIND, CandidateRecord, _party
from app.providers.election_history import (
    ELECTION_HISTORY_KIND,
    SeedCsvElectionHistoryProvider,
)
from app.providers.national_environment import (
    GENERIC_BALLOT_KIND,
    NormalizedGenericBallotPoll,
)
from app.providers.normalize import canonical_office, normalize_name
from app.providers.polls import POLL_KIND, NormalizedRacePoll


def _load(races_path: Path) -> dict:
    return json.loads(Path(races_path).read_text())


def _pd(v: Any) -> Optional[date]:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


class SeedFilePollProvider(BaseProvider):
    name = "seed_file_polls"
    kind = POLL_KIND
    endpoint_family = "seedfile:polls"

    def __init__(self, races_path: Path, **kw):
        super().__init__(**kw)
        self.races_path = Path(races_path)

    def enabled(self) -> bool:
        return self.races_path.exists()

    def _cache_params(self, **kwargs) -> dict:
        return {"path": str(self.races_path)}

    def _do_fetch(self, **kwargs):
        doc = _load(self.races_path)
        rows: list[dict] = []
        for race in doc.get("races", []):
            dem = race.get("dem_candidate")
            rep = race.get("rep_candidate")
            for p in race.get("polls", []):
                rows.append(
                    {
                        **p,
                        "state": race["state"],
                        "office": race["office"],
                        "cycle": race["cycle"],
                        "dem_candidate": p.get("dem_candidate") or (dem.get("name") if isinstance(dem, dict) else dem),
                        "rep_candidate": p.get("rep_candidate") or (rep.get("name") if isinstance(rep, dict) else rep),
                    }
                )
        if not rows:
            raise ProviderError(f"{self.name}: no polls in {self.races_path}")
        return rows, str(self.races_path), 200

    def _normalize(self, raw: Any, **kwargs) -> list[NormalizedRacePoll]:
        out: list[NormalizedRacePoll] = []
        for p in raw if isinstance(raw, list) else []:
            dem_name = str(p.get("dem_candidate") or "Democrat")
            rep_name = str(p.get("rep_candidate") or "Republican")
            out.append(
                NormalizedRacePoll(
                    pollster=str(p.get("pollster") or "Unknown"),
                    end_date=_pd(p.get("end_date")),
                    start_date=_pd(p.get("start_date")),
                    sample_size=p.get("sample_size"),
                    population=p.get("population"),
                    pollster_grade=p.get("pollster_grade"),
                    sponsor=p.get("partisan_sponsor") or p.get("sponsor"),
                    state=str(p.get("state") or "").upper()[:2],
                    office=canonical_office(p.get("office") or ""),
                    cycle=int(p.get("cycle") or 2026),
                    election_type="General",
                    answers=[
                        {"name": dem_name, "pct": float(p["dem_pct"])},
                        {"name": rep_name, "pct": float(p["rep_pct"])},
                    ],
                    source_url=p.get("source"),
                    provider=self.name,
                    provider_poll_id=p.get("poll_id"),
                )
            )
        return out


class SeedFileGenericBallotProvider(BaseProvider):
    name = "seed_file_generic_ballot"
    kind = GENERIC_BALLOT_KIND
    endpoint_family = "seedfile:generic_ballot"

    def __init__(self, races_path: Path, **kw):
        super().__init__(**kw)
        self.races_path = Path(races_path)

    def enabled(self) -> bool:
        return self.races_path.exists()

    def _cache_params(self, **kwargs) -> dict:
        return {"path": str(self.races_path)}

    def _do_fetch(self, **kwargs):
        doc = _load(self.races_path)
        rows = list(doc.get("generic_ballot", []))
        if not rows:
            raise ProviderError(f"{self.name}: no generic_ballot in {self.races_path}")
        return rows, str(self.races_path), 200

    def _normalize(self, raw: Any, **kwargs) -> list[NormalizedGenericBallotPoll]:
        out: list[NormalizedGenericBallotPoll] = []
        for g in raw if isinstance(raw, list) else []:
            out.append(
                NormalizedGenericBallotPoll(
                    pollster=str(g.get("pollster") or "Unknown"),
                    end_date=_pd(g.get("end_date")),
                    start_date=_pd(g.get("start_date")),
                    sample_size=g.get("sample_size"),
                    population=g.get("population"),
                    pollster_grade=g.get("pollster_grade"),
                    sponsor=g.get("sponsor"),
                    dem_pct=float(g["dem_pct"]),
                    rep_pct=float(g["rep_pct"]),
                    source_url=g.get("source"),
                    provider=self.name,
                    provider_poll_id=g.get("poll_id"),
                )
            )
        return out


class SeedFileCandidateProvider(BaseProvider):
    name = "seed_file_candidates"
    kind = CANDIDATE_KIND
    endpoint_family = "seedfile:candidates"

    def __init__(self, races_path: Path, **kw):
        super().__init__(**kw)
        self.races_path = Path(races_path)
        self._by_id = {r["race_id"]: r for r in _load(self.races_path).get("races", [])} if self.races_path.exists() else {}

    def enabled(self) -> bool:
        return bool(self._by_id)

    def _cache_params(self, *, race_id: str = "", **kwargs) -> dict:
        return {"race_id": race_id, "path": str(self.races_path)}

    def _do_fetch(self, *, race_id: str, **kwargs):  # type: ignore[override]
        cfg = self._by_id.get(race_id)
        if not cfg:
            raise ProviderError(f"{self.name}: no race {race_id}")
        return cfg, str(self.races_path), 200

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
                    name=str(c.get("name") or ""),
                    normalized_name=normalize_name(c.get("name")),
                    party=_party(c.get("party") or default_party),
                    is_incumbent=bool(c.get("is_incumbent", False)),
                    candidate_status=c.get("status", "confirmed"),
                    provider="seed_file",
                    source_url=c.get("source"),
                )
            )
        return out


def offline_poll_chain(races_path: Path) -> ProviderChain:
    return ProviderChain(POLL_KIND, [SeedFilePollProvider(races_path)])


def offline_generic_ballot_chain(races_path: Path) -> ProviderChain:
    return ProviderChain(GENERIC_BALLOT_KIND, [SeedFileGenericBallotProvider(races_path)])


def offline_election_history_chain() -> ProviderChain:
    return ProviderChain(ELECTION_HISTORY_KIND, [SeedCsvElectionHistoryProvider()])


def offline_candidate_chain_factory(races_path: Path):
    def _factory():
        return ProviderChain(CANDIDATE_KIND, [SeedFileCandidateProvider(races_path)])

    return _factory
