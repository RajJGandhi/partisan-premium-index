"""Historical presidential-result providers for state partisan lean (spec section 9).

The state-lean calculation needs, per year, the state's and the nation's Democratic-minus-
Republican presidential margin. This data changes essentially never, so it is cached in
``historical_election_results`` and only refreshed occasionally.

Fallback order: Decision Desk HQ Results API v4 (``/api/v4/race-calls``, OAuth) -> committed
seed CSVs. Missing -> the provider reports ``STALE``/``EMPTY`` and the engine treats state
lean as *absent* (never as 0).
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests

from app.config import get_settings
from app.providers.base import BaseProvider, ProviderChain, ProviderError

ELECTION_HISTORY_KIND = "election_history"

SEED_NATIONAL_CSV = Path("data/seed/historical_presidential_national.csv")
SEED_STATE_CSV = Path("data/seed/historical_presidential_state.csv")
DEFAULT_YEARS = (2016, 2020, 2024)

# Decision Desk HQ party ids (Results API v4). Numbers are stable; the string label varies.
_DDHQ_DEM_PARTY_IDS = {1, "1"}
_DDHQ_REP_PARTY_IDS = {2, "2"}
_DEM_LABELS = {"DEM", "DEMOCRATIC", "DEMOCRAT", "D", "DFL"}
_REP_LABELS = {"REP", "REPUBLICAN", "R", "GOP"}


def _races_from_payload(payload: Any) -> list[dict]:
    """DDHQ v4 has returned races as a bare list, ``{"data": [...]}``, or ``{"races": [...]}``
    across versions. Accept any of them; ignore anything that isn't a list of dicts."""
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = (
            payload.get("data")
            or payload.get("races")
            or payload.get("results")
            or payload.get("items")
            or []
        )
    else:
        rows = []
    return [r for r in rows if isinstance(r, dict)]


def _party_bucket(party: Any, party_id: Any) -> str | None:
    """Map a DDHQ party label / id to 'DEM' / 'REP' / None."""
    if party_id in _DDHQ_DEM_PARTY_IDS:
        return "DEM"
    if party_id in _DDHQ_REP_PARTY_IDS:
        return "REP"
    label = str(party or "").strip().upper()
    if label in _DEM_LABELS:
        return "DEM"
    if label in _REP_LABELS:
        return "REP"
    return None


def _candidate_party_map(race: dict) -> dict[str, str]:
    """Build ``{candidate_id: 'DEM'|'REP'}`` from a race item's candidate list.

    DDHQ has used ``candidates`` and ``participants``; each entry has an id under one of
    ``cand_id`` / ``candidate_id`` / ``id`` and a party under ``party`` / ``party_name`` /
    ``party_id``. Unknown parties are simply omitted (never guessed)."""
    out: dict[str, str] = {}
    entries = race.get("candidates") or race.get("participants") or []
    if not isinstance(entries, list):
        return out
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cand_id = entry.get("cand_id") or entry.get("candidate_id") or entry.get("id")
        if cand_id is None:
            continue
        bucket = _party_bucket(
            entry.get("party") or entry.get("party_name"),
            entry.get("party_id"),
        )
        if bucket:
            out[str(cand_id)] = bucket
    return out


@dataclass
class HistoricalResultRow:
    jurisdiction: str  # "US" or 2-letter state
    year: int
    office: str
    dem_margin_pct: Optional[float]
    dem_votes: Optional[float]
    rep_votes: Optional[float]
    provider: str
    source_url: Optional[str]


def _num(v: Any) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


class SeedCsvElectionHistoryProvider(BaseProvider):
    """Reads the committed national + (optional) state presidential-margin CSVs."""

    name = "seed_csv_election_history"
    kind = ELECTION_HISTORY_KIND
    endpoint_family = "seed:presidential_margins"

    def __init__(self, national_csv: Path | None = None, state_csv: Path | None = None, **kw):
        super().__init__(**kw)
        self.national_csv = national_csv or SEED_NATIONAL_CSV
        self.state_csv = state_csv or SEED_STATE_CSV

    def enabled(self) -> bool:
        return self.national_csv.exists() or self.state_csv.exists()

    def _cache_params(self, **kwargs) -> dict:
        return {"n": str(self.national_csv), "s": str(self.state_csv)}

    def _do_fetch(self, **kwargs) -> tuple[Any, str | None, int | None]:
        rows: list[dict] = []
        for path, default_juris in ((self.national_csv, "US"), (self.state_csv, None)):
            if not path.exists():
                continue
            with path.open() as fh:
                reader = csv.DictReader(row for row in fh if not row.lstrip().startswith("#"))
                for r in reader:
                    if not r:
                        continue
                    juris = (r.get("jurisdiction") or default_juris or "").strip().upper()
                    if not juris:
                        continue
                    rows.append({**r, "jurisdiction": juris, "_source": str(path)})
        if not rows:
            raise ProviderError(f"{self.name}: no rows in {self.national_csv} / {self.state_csv}")
        return rows, str(self.national_csv), 200

    def _normalize(self, raw: Any, **kwargs) -> list[HistoricalResultRow]:
        out: list[HistoricalResultRow] = []
        for r in raw if isinstance(raw, list) else []:
            year = _num(r.get("year"))
            if year is None:
                continue
            out.append(
                HistoricalResultRow(
                    jurisdiction=str(r.get("jurisdiction")).upper(),
                    year=int(year),
                    office=(r.get("office") or "president").strip().lower(),
                    dem_margin_pct=_num(r.get("dem_margin_pct")),
                    dem_votes=_num(r.get("dem_votes")),
                    rep_votes=_num(r.get("rep_votes")),
                    provider=self.name,
                    source_url=r.get("source_note") or r.get("_source"),
                )
            )
        return out


class DecisionDeskHqElectionHistoryProvider(BaseProvider):
    """Decision Desk HQ Results API v4 -> per-state + national presidential margins.

    Auth: OAuth2 client-credentials (``POST /api/v4/oauth/token`` with client id + secret ->
    short-lived JWT, cached in-process), or a pre-issued static bearer in ``DECISIONDESK_API_KEY``.
    Data: ``GET /api/v4/race-calls?year=&office_id=1&name=General Election`` (paginated, limit
    250) -- this endpoint carries both ``candidates[]`` (with ``party_id`` / ``party_name``) and
    ``topline_results.votes`` ({candidate_id: count}) on one object, so a state's D-minus-R
    margin is a single join. Disabled (falls through to the seed CSVs) until credentials are set.
    Field handling is defensive across the v4 revisions DDHQ has shipped; confirm against
    ``docs.decisiondeskhq.com`` when wiring a live key."""

    name = "decisiondesk_election_history"
    kind = ELECTION_HISTORY_KIND
    endpoint_family = "ddhq:results_v4_race_calls"

    # office_id 4 = US Senate, 2 = Governor, 1 = President (v4 offices enum).
    PRESIDENT_OFFICE_ID = 1

    #: OAuth token cache, keyed by client_id -> (access_token, expires_at_epoch). Module-level so
    #: it survives across provider instances within a process.
    _token_cache: dict[str, tuple[str, float]] = {}

    def __init__(self, years: tuple[int, ...] = DEFAULT_YEARS, **kw):
        super().__init__(**kw)
        self.years = years
        s = get_settings()
        self.base_url = s.decisiondesk_results_base_url.rstrip("/")
        self.client_id = s.decisiondesk_client_id
        self.client_secret = s.decisiondesk_client_secret
        self.static_bearer = s.decisiondesk_api_key

    def enabled(self) -> bool:
        return bool(self.base_url and (self.static_bearer or (self.client_id and self.client_secret)))

    def _cache_params(self, **kwargs) -> dict:
        return {"years": ",".join(map(str, self.years))}

    def _bearer(self) -> str:
        if self.static_bearer:
            return self.static_bearer
        cached = self._token_cache.get(self.client_id)
        if cached and cached[1] > time.time() + 30:
            return cached[0]
        try:
            resp = requests.post(
                f"{self.base_url}/api/v4/oauth/token",
                json={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                },
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"{self.name}: OAuth token request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"{self.name}: OAuth token endpoint returned {resp.status_code}")
        body = resp.json()
        token = body.get("access_token") or body.get("token")
        if not token:
            raise ProviderError(f"{self.name}: OAuth response had no access_token")
        expires = time.time() + float(body.get("expires_in", 3300))
        self._token_cache[self.client_id] = (token, expires)
        return token

    def _do_fetch(self, **kwargs) -> tuple[Any, str | None, int | None]:
        headers = {"Authorization": f"Bearer {self._bearer()}"}
        url = f"{self.base_url}/api/v4/race-calls"
        all_races: list[dict] = []
        for year in self.years:
            page = 1
            while page <= 20:  # hard cap; ~56 presidential state races per year at limit 250
                payload, _status = self._http_get_json(
                    url,
                    params={
                        "year": year,
                        "office_id": self.PRESIDENT_OFFICE_ID,
                        "name": "General Election",
                        "limit": 250,
                        "page": page,
                    },
                    headers=headers,
                )
                races = _races_from_payload(payload)
                if not races:
                    break
                all_races.extend(races)
                total_pages = payload.get("total_pages") if isinstance(payload, dict) else None
                if (isinstance(total_pages, int) and page >= total_pages) or len(races) < 250:
                    break
                page += 1
        if not all_races:
            raise ProviderError(f"{self.name}: no presidential races returned for years {self.years}")
        return all_races, url, 200

    def _tally(self, race: dict) -> tuple[float, float]:
        """Return ``(dem_votes, rep_votes)`` for one race item, 0.0 where unknown."""
        party_by_cand = _candidate_party_map(race)
        votes = ((race.get("topline_results") or {}).get("votes")) or {}
        dem = rep = 0.0
        if isinstance(votes, dict):
            for cand_id, count in votes.items():
                bucket = party_by_cand.get(str(cand_id))
                if bucket == "DEM":
                    dem += _num(count) or 0.0
                elif bucket == "REP":
                    rep += _num(count) or 0.0
        return dem, rep

    def _normalize(self, raw: Any, **kwargs) -> list[HistoricalResultRow]:
        # ME / NE split their electoral votes, so DDHQ also carries district-level presidential
        # "General Election" rows (district = "1", "2", ...). Keep only the statewide roll-up:
        # skip rows with a district, and if a (year, state) still has duplicates keep the one
        # with the most votes.
        best: dict[tuple[int, str], tuple[float, float, Any]] = {}
        for race in raw if isinstance(raw, list) else []:
            if not isinstance(race, dict) or race.get("test_data"):
                continue
            if str(race.get("district") or "").strip() not in {"", "0", "None"}:
                continue
            year = _num(race.get("year"))
            state = str(race.get("state") or "").upper()[:2]
            if year is None or not state:
                continue
            dem, rep = self._tally(race)
            if dem <= 0 and rep <= 0:
                continue
            key = (int(year), state)
            if key not in best or (dem + rep) > (best[key][0] + best[key][1]):
                best[key] = (dem, rep, race.get("race_id"))

        out: list[HistoricalResultRow] = []
        national: dict[int, list[float]] = {}  # year -> [dem_total, rep_total]
        for (year, state), (dem, rep, race_id) in sorted(best.items()):
            acc = national.setdefault(year, [0.0, 0.0])
            acc[0] += dem
            acc[1] += rep
            out.append(
                HistoricalResultRow(
                    jurisdiction=state,
                    year=year,
                    office="president",
                    dem_margin_pct=100.0 * (dem - rep) / (dem + rep) if (dem + rep) > 0 else None,
                    dem_votes=dem or None,
                    rep_votes=rep or None,
                    provider=self.name,
                    source_url=f"{self.base_url}/api/v4/race/{race_id}",
                )
            )
        # Derive the national popular-vote margin by summing the state tallies we just parsed.
        for year, (dem_total, rep_total) in sorted(national.items()):
            if dem_total + rep_total <= 0:
                continue
            out.append(
                HistoricalResultRow(
                    jurisdiction="US",
                    year=year,
                    office="president",
                    dem_margin_pct=100.0 * (dem_total - rep_total) / (dem_total + rep_total),
                    dem_votes=dem_total,
                    rep_votes=rep_total,
                    provider=self.name,
                    source_url=f"{self.base_url}/api/v4/race-calls?year={year}&office_id=1",
                )
            )
        return out


def default_election_history_chain(
    *, years: tuple[int, ...] = DEFAULT_YEARS, national_csv: Path | None = None, state_csv: Path | None = None
) -> ProviderChain:
    return ProviderChain(
        ELECTION_HISTORY_KIND,
        [
            DecisionDeskHqElectionHistoryProvider(years=years),
            SeedCsvElectionHistoryProvider(national_csv=national_csv, state_csv=state_csv),
        ],
    )
