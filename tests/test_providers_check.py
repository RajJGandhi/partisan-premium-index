"""Smoke tests for scripts/check_providers.py -- the provider inventory / reachability report.

The inventory path must be pure (no network, no DB) so it is safe to run anywhere; only
``--probe`` makes requests.
"""

from __future__ import annotations

import json

import scripts.check_providers as cp


def test_collect_lists_every_chain_provider_without_io():
    rows = cp._collect()
    names = {r.name for r in rows}
    # one row per distinct provider across the poll / generic-ballot / history / candidate chains
    assert {
        "votehub_race_polls", "decisiondesk_ballot_test", "pollingsource_polls", "web_search_polls",
        "votehub_generic_ballot", "decisiondesk_generic_ballot",
        "decisiondesk_election_history", "seed_csv_election_history",
        "openfec_candidates", "seed_candidates",
        "polymarket_gamma_discovery",
    } <= names
    for r in rows:
        assert isinstance(r.enabled, bool)
        assert r.gate  # every row explains why it is on/off
        assert r.probe is None  # no probing on the plain inventory


def test_disabled_rows_name_their_gating_env_var():
    rows = {r.name: r for r in cp._collect()}
    assert not rows["decisiondesk_election_history"].enabled
    assert "DECISIONDESK_CLIENT_ID" in rows["decisiondesk_election_history"].gate
    assert not rows["openfec_candidates"].enabled
    assert "FEC_API_KEY" in rows["openfec_candidates"].gate


def test_json_report_is_valid_and_probeless(capsys):
    rc = cp.main.__wrapped__ if hasattr(cp.main, "__wrapped__") else cp.main
    import sys

    argv = sys.argv
    sys.argv = ["check_providers.py", "--json"]
    try:
        exit_code = rc()
    finally:
        sys.argv = argv
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list) and payload
    assert all("probe" not in row for row in payload)
    assert {"kind", "name", "endpoint_family", "enabled", "gate"} <= set(payload[0])
