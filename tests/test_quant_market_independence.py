"""The Quant forecast must be structurally blind to all prediction-market information
(spec sections 17, 18, 22, 52)."""

from __future__ import annotations

import ast
import inspect
import pathlib
import pkgutil

import pytest

import app.quant as quant_pkg
from app.quant.engine import run_quant_forecast
from app.quant.evidence_bundle import build_quant_evidence_bundle
from app.quant.types import FORBIDDEN_INPUT_KEYS, assert_market_free, iter_forbidden_hits

QUANT_DIR = pathlib.Path(quant_pkg.__file__).parent


def test_run_quant_forecast_signature_has_no_market_argument():
    sig = inspect.signature(run_quant_forecast)
    params = set(sig.parameters)
    assert params == {"inp", "cfg"}
    for banned in ("market", "price", "market_probability", "polymarket", "bid", "ask", "spread"):
        assert not any(banned in p for p in params)


def test_no_quant_module_imports_market_code():
    """Static check: nothing under app/quant imports Polymarket / market-snapshot code."""
    banned_imports = {
        "app.ppi.polymarket",
        "app.ingest.polymarket_gamma",
        "app.ingest.polymarket_clob",
        "app.ingest.kalshi",
        "app.ingest.predictit",
    }
    offenders = []
    for py in QUANT_DIR.rglob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
            elif isinstance(node, ast.Import):
                mod = ",".join(a.name for a in node.names)
            else:
                continue
            for b in banned_imports:
                if b in mod:
                    offenders.append(f"{py.name}: imports {mod}")
            if "MarketSnapshot" in mod:
                offenders.append(f"{py.name}: imports {mod}")
    assert offenders == [], offenders


def test_quant_package_modules_all_import_clean():
    for mod in pkgutil.walk_packages(quant_pkg.__path__, prefix="app.quant."):
        __import__(mod.name)  # must not pull market code transitively


def test_forbidden_keys_cover_the_obvious_market_fields():
    for key in (
        "market_probability",
        "polymarket_probability",
        "yes_best_bid",
        "yes_best_ask",
        "midpoint",
        "spread",
        "volume",
        "liquidity",
        "last_trade_price",
        "raw_ppi",
        "market_model_spread",
    ):
        assert key in FORBIDDEN_INPUT_KEYS


def test_assert_market_free_rejects_nested_market_fields():
    with pytest.raises(ValueError):
        assert_market_free({"race": {"polls": [{"spread": 0.02}]}})
    with pytest.raises(ValueError):
        assert_market_free([{"ok": 1}, {"deep": {"yes_midpoint": 0.5}}])
    assert list(iter_forbidden_hits({"a": {"volume": 1}})) == ["root.a.volume"]
    # clean payloads pass silently
    assert_market_free({"polls": [{"dem_pct": 52, "rep_pct": 44, "sample_size": 900}]})


def test_engine_raises_if_a_market_field_is_smuggled_via_override_container(make_input):
    """national_environment_override must be a plain number; a dict carrying a market key is caught."""
    from app.quant.types import QuantForecastInput

    base = make_input()
    with pytest.raises(ValueError):
        QuantForecastInput(
            race=base.race,
            as_of=base.as_of,
            state_history=base.state_history,
            national_environment_override={"market_probability": 0.9},  # type: ignore[arg-type]
        )


def test_evidence_bundle_is_market_free(make_input, make_poll):
    inp = make_input(
        polls=[make_poll(pollster="A"), make_poll(pollster="B", end_date=None) if False else make_poll(pollster="B")],
        dem_incumbent=True,
    )
    result = run_quant_forecast(inp)
    bundle = build_quant_evidence_bundle(inp, result)
    # building it already runs assert_market_free; double-check no forbidden key slipped in
    assert list(iter_forbidden_hits(bundle.payload)) == []
    assert len(bundle.content_hash) == 64


def test_result_public_dict_has_no_market_fields(make_input, make_poll):
    inp = make_input(polls=[make_poll()], dem_incumbent=True)
    d = run_quant_forecast(inp).as_public_dict()
    assert list(iter_forbidden_hits(d)) == []
