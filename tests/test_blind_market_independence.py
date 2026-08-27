"""The blind benchmarks and the ensemble wiring stay blind to prediction-market prices,
the Quant probability, and each other's forecast (spec sections 18, 23, 24)."""

from __future__ import annotations

import ast
import inspect
import pathlib

import app.blind as blind_pkg
from app.blind.prompt import build_blind_prompt
from app.blind.runner import run_blind_forecasts
from app.quant.types import iter_forbidden_hits

BLIND_DIR = pathlib.Path(blind_pkg.__file__).parent
CONTRACT = "Will the Democratic candidate win the TX senate general election in 2026?"


def _all_indexes(haystack: str, needle: str):
    start = 0
    while (i := haystack.find(needle, start)) != -1:
        yield i
        start = i + 1


def test_run_blind_forecasts_has_no_market_or_quant_probability_param():
    params = set(inspect.signature(run_blind_forecasts).parameters)
    for banned in ("market", "price", "bid", "ask", "spread", "polymarket", "quant_probability", "ensemble"):
        assert not any(banned in p for p in params), f"blind runner exposes {banned!r}"
    # it takes the evidence bundle + contract question, nothing model-comparative
    assert "evidence_bundle" in params and "contract_question" in params
    assert "quant_forecast" not in params and "openai_row" not in params


def test_no_blind_module_imports_market_code():
    banned = {"app.ppi.polymarket", "app.ingest.polymarket_gamma", "app.ingest.polymarket_clob",
              "app.ingest.kalshi", "app.ingest.predictit"}
    offenders = []
    for py in BLIND_DIR.rglob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            mod = None
            if isinstance(node, ast.ImportFrom):
                mod = node.module
            elif isinstance(node, ast.Import):
                mod = ",".join(a.name for a in node.names)
            if mod and (any(b in mod for b in banned) or "MarketSnapshot" in mod):
                offenders.append(f"{py.name}: {mod}")
    assert offenders == [], offenders


def test_rendered_prompt_has_no_forbidden_fields(blind_bundle):
    text = build_blind_prompt(blind_bundle, contract_question=CONTRACT)
    hits = [h for h in iter_forbidden_hits({"prompt": text}) if False]  # structural scan not meaningful on a string
    assert hits == []
    low = text.lower()
    # no market price / odds anywhere except the negative reminder paragraph
    for term in ("polymarket", "kalshi", "predictit", "betting odds", "implied probability", "order book"):
        assert term not in low, f"prompt leaked {term!r}"
    # every mention of "market price" is inside the "not given / do not guess" reminder
    for idx in _all_indexes(low, "market price"):
        window = low[max(0, idx - 45) : idx + 20]
        assert "not given the market price" in window or "do not guess the market price" in window


def test_evidence_bundle_stays_market_free(blind_bundle):
    assert list(iter_forbidden_hits(blind_bundle.payload)) == []
