"""Historical presidential-result providers for state partisan lean (spec section 9).

The state-lean calculation needs, per year, the state's and the nation's Democratic-minus-
Republican presidential margin. This data changes essentially never, so it is cached in
``historical_election_results`` and only refreshed occasionally.

Fallback order: Decision Desk HQ results API -> committed seed CSVs. Missing -> the provider
reports ``STALE``/``EMPTY`` and the engine treats state lean as *absent* (never as 0).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings
from app.providers.base import BaseProvider, ProviderChain, ProviderError

ELECTION_HISTORY_KIND = "election_history"

SEED_NATIONAL_CSV = Path("data/seed/historical_presidential_national.csv")
SEED_STATE_CSV = Path("data/seed/historical_presidential_state.csv")
DEFAULT_YEARS = (2016, 2020, 2024)


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
    """DDHQ results API. Disabled unless ``DECISIONDESK_RESULTS_BASE_URL`` is configured, since the
    exact historical-results endpoint/shape must be confirmed against current docs + a key."""

    name = "decisiondesk_election_history"
    kind = ELECTION_HISTORY_KIND
    endpoint_family = "ddhq:presidential_history"

    def __init__(self, years: tuple[int, ...] = DEFAULT_YEARS, **kw):
        super().__init__(**kw)
        self.years = years
        s = get_settings()
        self.base_url = s.decisiondesk_results_base_url.rstrip("/")
        self.api_key = s.decisiondesk_api_key

    def enabled(self) -> bool:
        return bool(self.base_url)

    def _cache_params(self, **kwargs) -> dict:
        return {"years": ",".join(map(str, self.years))}

    def _do_fetch(self, **kwargs) -> tuple[Any, str | None, int | None]:
        url = f"{self.base_url}/v4/elections/president"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        payload, status = self._http_get_json(
            url, params={"years": ",".join(map(str, self.years))}, headers=headers
        )
        return payload, url, status

    def _normalize(self, raw: Any, **kwargs) -> list[HistoricalResultRow]:
        # Defensive: accept either {"results":[...]} or a bare list of {jurisdiction, year, dem_pct, rep_pct}.
        rows = raw.get("results", raw) if isinstance(raw, dict) else raw
        out: list[HistoricalResultRow] = []
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict):
                continue
            year = _num(r.get("year") or r.get("cycle"))
            juris = str(r.get("jurisdiction") or r.get("state") or ("US" if r.get("national") else "")).upper()
            if year is None or not juris:
                continue
            dem_pct, rep_pct = _num(r.get("dem_pct") or r.get("dem_share")), _num(r.get("rep_pct") or r.get("rep_share"))
            margin = _num(r.get("dem_margin_pct"))
            if margin is None and dem_pct is not None and rep_pct is not None:
                margin = dem_pct - rep_pct
            out.append(
                HistoricalResultRow(
                    jurisdiction=juris[:2] if juris != "US" else "US",
                    year=int(year),
                    office="president",
                    dem_margin_pct=margin,
                    dem_votes=_num(r.get("dem_votes")),
                    rep_votes=_num(r.get("rep_votes")),
                    provider=self.name,
                    source_url=r.get("source") or (self.base_url or None),
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
