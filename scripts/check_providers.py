"""Inventory + reachability check for the PPI data-acquisition providers (spec sections 5-10).

    PYTHONPATH=. python scripts/check_providers.py            # config / enabled report (offline)
    PYTHONPATH=. python scripts/check_providers.py --probe     # + a live reachability request each
    PYTHONPATH=. python scripts/check_providers.py --json      # machine-readable

For every provider in the real ingest chains it prints:

  * the provider name, kind and endpoint family,
  * whether ``enabled()`` is true and, if not, which env var would switch it on,
  * with ``--probe``: the result of one cheap request -- OK / HTTP <code> / AUTH_FAIL /
    UNREACHABLE / NO_PROBE / SKIPPED (disabled).

Nothing here writes to the database or mutates state. A non-zero exit means at least one
*enabled* provider failed its probe (disabled providers never fail the run).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from functools import partial
from typing import Callable, Optional

import requests

from app.config import get_settings
from app.providers.candidates import default_candidate_chain
from app.providers.election_history import (
    DEFAULT_YEARS,
    DecisionDeskHqElectionHistoryProvider,
    default_election_history_chain,
)
from app.providers.national_environment import default_generic_ballot_chain
from app.providers.polls import default_poll_chain

CYCLE = 2026
TIMEOUT = 12


@dataclass
class ProbeOutcome:
    status: str  # OK / HTTP_4XX / HTTP_5XX / AUTH_FAIL / UNREACHABLE / NO_PROBE / SKIPPED
    detail: str = ""

    @property
    def failed(self) -> bool:
        return self.status in {"HTTP_4XX", "HTTP_5XX", "AUTH_FAIL", "UNREACHABLE"}


@dataclass
class Row:
    kind: str
    name: str
    endpoint_family: str
    enabled: bool
    gate: str  # human note: what makes it enabled / why it is off
    probe: Optional[ProbeOutcome] = None

    def as_dict(self) -> dict:
        d = {
            "kind": self.kind,
            "name": self.name,
            "endpoint_family": self.endpoint_family,
            "enabled": self.enabled,
            "gate": self.gate,
        }
        if self.probe is not None:
            d["probe"] = {"status": self.probe.status, "detail": self.probe.detail}
        return d


# --------------------------------------------------------------------------------------------------
# Probes: one cheap request per provider. Kept here (not on the provider) so a dry inventory never
# does I/O and so we can hit a health/list path instead of a full fetch.
# --------------------------------------------------------------------------------------------------
def _get(url: str, **kw) -> requests.Response:
    kw.setdefault("timeout", TIMEOUT)
    kw.setdefault("headers", {}).setdefault("User-Agent", get_settings().source_user_agent)
    return requests.get(url, **kw)


def _classify(resp: requests.Response) -> ProbeOutcome:
    if resp.status_code in (401, 403):
        return ProbeOutcome("AUTH_FAIL", f"HTTP {resp.status_code}")
    if 400 <= resp.status_code < 500:
        return ProbeOutcome("HTTP_4XX", f"HTTP {resp.status_code}")
    if resp.status_code >= 500:
        return ProbeOutcome("HTTP_5XX", f"HTTP {resp.status_code}")
    return ProbeOutcome("OK", f"HTTP {resp.status_code}")


def _probe_wrap(fn: Callable[[], ProbeOutcome]) -> ProbeOutcome:
    try:
        return fn()
    except requests.RequestException as exc:
        return ProbeOutcome("UNREACHABLE", type(exc).__name__)
    except Exception as exc:  # never let a probe crash the report
        return ProbeOutcome("UNREACHABLE", f"{type(exc).__name__}: {exc}")


def _probe_ddhq_polling(family: str) -> ProbeOutcome:
    base = get_settings().decisiondesk_polling_base_url.rstrip("/")
    path = "generic_ballot" if "generic" in family else "ballot_test"
    return _classify(_get(f"{base}/api/v1/polls/{path}"))


def _probe_ddhq_results(_family: str) -> ProbeOutcome:
    s = get_settings()
    p = DecisionDeskHqElectionHistoryProvider(years=DEFAULT_YEARS)
    if not p.enabled():
        return ProbeOutcome("SKIPPED", "no client id/secret or static bearer")
    try:
        token = p._bearer()  # performs the OAuth exchange (or returns the static bearer)
    except Exception as exc:  # noqa: BLE001 - surfaced as AUTH_FAIL below
        return ProbeOutcome("AUTH_FAIL", f"token exchange: {exc}")
    resp = _get(
        f"{s.decisiondesk_results_base_url.rstrip('/')}/api/v4/race-calls",
        params={"year": DEFAULT_YEARS[-1], "office_id": 1, "limit": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    return _classify(resp)


def _probe_votehub(family: str) -> ProbeOutcome:
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.votehub_api_key}"} if s.votehub_api_key else {}
    poll_type = "us-senator" if "race_polls" in family else "generic-ballot"
    return _classify(
        _get(
            f"{s.votehub_api_base_url.rstrip('/')}/polls",
            params={"poll_type": poll_type, "from_date": f"{CYCLE - 1}-01-01"},
            headers=headers,
        )
    )


def _probe_pollingsource_polls(_family: str) -> ProbeOutcome:
    s = get_settings()
    if not s.pollingsource_api_base_url:
        return ProbeOutcome("SKIPPED", "POLLINGSOURCE_API_BASE_URL unset")
    headers = {"Authorization": f"Bearer {s.pollingsource_api_key}"} if s.pollingsource_api_key else {}
    return _classify(
        _get(f"{s.pollingsource_api_base_url.rstrip('/')}/polls",
             params={"cycle": str(CYCLE), "type": "general"}, headers=headers)
    )


def _probe_pollingsource_gb(_family: str) -> ProbeOutcome:
    s = get_settings()
    if not s.pollingsource_api_base_url:
        return ProbeOutcome("SKIPPED", "POLLINGSOURCE_API_BASE_URL unset")
    headers = {"Authorization": f"Bearer {s.pollingsource_api_key}"} if s.pollingsource_api_key else {}
    return _classify(
        _get(f"{s.pollingsource_api_base_url.rstrip('/')}/generic-ballot",
             params={"cycle": str(CYCLE)}, headers=headers)
    )


def _probe_openfec(_family: str) -> ProbeOutcome:
    s = get_settings()
    if not s.fec_api_key:
        return ProbeOutcome("SKIPPED", "FEC_API_KEY unset")
    base = (s.openfec_base_url or "https://api.open.fec.gov/v1").rstrip("/")
    return _classify(
        _get(f"{base}/candidates/search/",
             params={"api_key": s.fec_api_key, "office": "S", "per_page": 1})
    )


def _probe_polymarket_gamma(_family: str) -> ProbeOutcome:
    base = get_settings().polymarket_gamma_base_url.rstrip("/")
    return _classify(_get(f"{base}/events", params={"limit": 1, "active": "true"}))


PROBES: dict[str, Callable[[str], ProbeOutcome]] = {
    "decisiondesk_ballot_test": _probe_ddhq_polling,
    "decisiondesk_generic_ballot": _probe_ddhq_polling,
    "decisiondesk_election_history": _probe_ddhq_results,
    "votehub_generic_ballot": _probe_votehub,
    "votehub_race_polls": _probe_votehub,
    "pollingsource_polls": _probe_pollingsource_polls,
    "pollingsource_generic_ballot": _probe_pollingsource_gb,
    "openfec_candidates": _probe_openfec,
    "polymarket_gamma_discovery": _probe_polymarket_gamma,
}

# Which env var(s) gate a disabled provider -- shown in the report so the fix is obvious.
GATE_ENV: dict[str, str] = {
    "decisiondesk_election_history": "DECISIONDESK_CLIENT_ID + DECISIONDESK_CLIENT_SECRET (or DECISIONDESK_API_KEY)",
    "votehub_generic_ballot": "VOTEHUB_API_BASE_URL",
    "pollingsource_polls": "POLLINGSOURCE_API_BASE_URL",
    "pollingsource_generic_ballot": "POLLINGSOURCE_API_BASE_URL",
    "openfec_candidates": "FEC_API_KEY",
    "web_search_polls": "an injected web-search extractor (wired by the orchestrator)",
    "web_search_generic_ballot": "an injected web-search extractor (wired by the orchestrator)",
    "web_search_candidates": "an injected web-search extractor (wired by the orchestrator)",
    "seed_candidates": "per-race seed candidates (populated from the race config at ingest time)",
}


def _collect() -> list[Row]:
    settings = get_settings()
    chains = [
        default_poll_chain(cycle=CYCLE),
        default_generic_ballot_chain(cycle=CYCLE),
        default_election_history_chain(years=DEFAULT_YEARS),
        default_candidate_chain(race_config={}),
    ]
    rows: list[Row] = []
    seen: set[str] = set()
    for chain in chains:
        for prov in chain.providers:
            if prov.name in seen:
                continue
            seen.add(prov.name)
            enabled = bool(prov.enabled())
            if enabled:
                gate = "enabled"
            else:
                gate = f"disabled -- set {GATE_ENV.get(prov.name, 'its base URL / key in .env')}"
            rows.append(
                Row(
                    kind=prov.kind,
                    name=prov.name,
                    endpoint_family=prov.endpoint_family,
                    enabled=enabled,
                    gate=gate,
                )
            )
    # Market discovery is not part of the ingest chains; report it explicitly.
    rows.append(
        Row(
            kind="market_discovery",
            name="polymarket_gamma_discovery",
            endpoint_family="polymarket:gamma_events",
            enabled=bool(settings.polymarket_gamma_base_url),
            gate="enabled" if settings.polymarket_gamma_base_url else "disabled -- set POLYMARKET_GAMMA_BASE_URL",
        )
    )
    return rows


def _run_probes(rows: list[Row]) -> None:
    for row in rows:
        if not row.enabled:
            row.probe = ProbeOutcome("SKIPPED", "provider disabled")
            continue
        fn = PROBES.get(row.name)
        if fn is None:
            row.probe = ProbeOutcome("NO_PROBE", "no reachability check defined")
            continue
        row.probe = _probe_wrap(partial(fn, row.endpoint_family))


_ICON = {
    "OK": "ok ", "SKIPPED": "-- ", "NO_PROBE": "?? ",
    "AUTH_FAIL": "AUTH", "HTTP_4XX": "4xx", "HTTP_5XX": "5xx", "UNREACHABLE": "DOWN",
}


def _print_human(rows: list[Row], probed: bool) -> None:
    kind = None
    for row in sorted(rows, key=lambda r: (r.kind, r.name)):
        if row.kind != kind:
            kind = row.kind
            print(f"\n== {kind} ==")
        flag = "on " if row.enabled else "off"
        line = f"  [{flag}] {row.name:<30} {row.endpoint_family}"
        if probed and row.probe is not None:
            line += f"\n         probe: {_ICON.get(row.probe.status, '   ')} {row.probe.status}"
            if row.probe.detail:
                line += f" ({row.probe.detail})"
        if not row.enabled:
            line += f"\n         {row.gate}"
        print(line)
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="store_true", help="also make one live request per enabled provider")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of the text report")
    args = ap.parse_args()

    rows = _collect()
    if args.probe:
        _run_probes(rows)

    if args.json:
        print(json.dumps([r.as_dict() for r in rows], indent=2))
    else:
        _print_human(rows, probed=args.probe)

    failures = [(r.name, r.probe.status) for r in rows if r.probe is not None and r.probe.failed]
    if failures:
        print(f"{len(failures)} enabled provider(s) failed their probe: "
              + ", ".join(f"{name}={status}" for name, status in failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
