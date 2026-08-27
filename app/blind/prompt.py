"""Blind-benchmark prompt construction (spec sections 22, 23, 24).

The prompt is rendered **only** from the timestamp-locked, market-free
:class:`app.quant.types.EvidenceBundle`. ``assert_prompt_market_free`` re-scans the rendered text
for any forbidden market term before it is ever sent, so a future bundle-field addition cannot leak
a market price into a blind forecast. Quant's probability, the other model's forecast, and the
ensemble are never part of the prompt.
"""

from __future__ import annotations

import hashlib
import json
import re

from app.quant.types import FORBIDDEN_INPUT_KEYS, EvidenceBundle, assert_market_free

PROMPT_VERSION = "blind_benchmark_v1"

SYSTEM_INSTRUCTIONS = """You are a calibrated, independent election-forecasting analyst.

Independently estimate the probability that the stated binary election event resolves YES.

You have NO access to prediction-market information of any kind -- no Polymarket, Kalshi, PredictIt,
Manifold, betting exchange, or aggregator prices, odds, or implied probabilities. Do not infer,
recall, or invent any market price. Use ONLY the supplied evidence packet.

Think like a disciplined forecaster:
- Start from historical / structural base rates.
- Separate hard evidence from narrative and speculation.
- Weigh polling quality, recency, and quantity; do not over-react to a single poll.
- Be honest about uncertainty; if the evidence is genuinely insufficient, set should_abstain=true
  (still give your best probability, but say why confidence is low).

Return ONLY a single valid JSON object matching the schema. No markdown, no commentary outside the
JSON object."""


def _render_polls(bundle_payload: dict) -> str:
    polls = bundle_payload.get("polls") or []
    if not polls:
        return "  (no usable polls)"
    lines = []
    for p in polls[:25]:
        lines.append(
            f"  - {p.get('pollster')}: {p.get('start_date')}..{p.get('end_date')}, "
            f"n={p.get('sample_size')} {p.get('population') or '?'}, grade={p.get('pollster_grade') or '?'}, "
            f"sponsor={p.get('partisan_sponsor') or 'none'}{' [internal]' if p.get('internal') else ''} -> "
            f"D {p.get('dem_pct')} / R {p.get('rep_pct')} (D margin {p.get('margin_dem')})"
        )
    return "\n".join(lines)


def _render_fundamentals(bundle_payload: dict) -> str:
    f = bundle_payload.get("fundamentals") or {}
    pa = bundle_payload.get("polling_average") or {}
    ne = bundle_payload.get("national_environment") or {}
    parts = [
        f"  state_lean: {f.get('state_lean')}",
        f"  national_environment (generic ballot, D margin pts): {ne.get('value') if ne else None}",
        f"  incumbency adjustment (pts): {f.get('incumbency_adjustment')}",
        f"  fundamentals margin (D pts): {f.get('fundamental_margin')}",
        f"  weighted polling margin (D pts): {pa.get('polling_margin')}",
        f"  effective poll count: {pa.get('n_eff')}, latest poll: {pa.get('latest_poll_date')}",
    ]
    return "\n".join(parts)


def _render_news(bundle_payload: dict) -> str:
    news = bundle_payload.get("current_news") or []
    if not news:
        return "  (no material race-specific news collected)"
    lines = []
    for n in news[:15]:
        lines.append(f"  - [{n.get('category') or 'context'}] {n.get('title')} ({n.get('source_domain') or n.get('url') or ''})")
        if n.get("summary"):
            lines.append(f"      {n['summary'][:400]}")
    return "\n".join(lines)


def build_blind_prompt(bundle: EvidenceBundle, *, contract_question: str) -> str:
    """Render the user-turn prompt from the evidence bundle. Raises if any market term is present."""
    payload = bundle.payload
    assert_market_free(payload, path="blind_prompt.bundle")  # defense in depth over EvidenceBundle.build
    race = payload.get("race", {})
    cm = payload.get("candidate_metadata", {})
    text = f"""BINARY EVENT TO FORECAST:
{contract_question.strip()}

RACE:
  {race.get('state')} {race.get('office')} {race.get('cycle')}, election date {payload.get('election_date')}
  Democratic candidate: {(cm.get('dem') or {}).get('name')} (status {(cm.get('dem') or {}).get('status')})
  Republican candidate: {(cm.get('rep') or {}).get('name')} (status {(cm.get('rep') or {}).get('status')})
  incumbent party: {(payload.get('incumbency') or {}).get('incumbent_party')}
  candidate-mapping confidence: {cm.get('mapping_confidence')}

FORECAST TIMESTAMP (evidence cutoff): {payload.get('forecast_timestamp')}

POLLS (individual releases; you compute your own read):
{_render_polls(payload)}

FUNDAMENTALS CONTEXT (transparent, provisional -- weigh as you see fit):
{_render_fundamentals(payload)}

STATE PARTISAN HISTORY:
  {json.dumps(payload.get('state_history', {}).get('state_lean_detail'), default=str)[:800]}

MATERIAL RACE NEWS (contamination-filtered; no market/betting sources):
{_render_news(payload)}

REMINDER: You are not given the market price, bid, ask, spread, volume, or order-book data for any
contract. Do not guess the market price. Estimate only your own fair probability that the event
above resolves YES.

Return a JSON object with keys: probability (0-1), should_abstain (bool), rationale (string),
uncertainty_drivers (array of short strings), base_rate_notes (string)."""
    assert_prompt_market_free(text)
    return text


_MARKET_TERM_RE = re.compile(
    r"\b(polymarket|kalshi|predictit|manifold|betting\s+odds|prediction\s+market|"
    r"implied\s+probabilit|market\s+price|order[- ]?book|best\s+bid|best\s+ask|midpoint|"
    r"\braw_ppi\b|partisan_premium|market_model_spread)\b",
    re.IGNORECASE,
)


def assert_prompt_market_free(text: str) -> None:
    """Raise if the rendered prompt mentions any prediction-market term or forbidden field key."""
    m = _MARKET_TERM_RE.search(text)
    if m:
        # allow the single deliberate negative reminder sentence ("You are not given the market
        # price ...") -- everything else is a leak.
        span = text[max(0, m.start() - 40) : m.end() + 5].lower()
        if "not given the market price" not in span and "do not guess the market price" not in span:
            raise ValueError(f"blind prompt leaked a prediction-market term: {m.group(0)!r}")
    lowered = text.lower()
    for key in FORBIDDEN_INPUT_KEYS:
        if key in ("spread", "volume", "midpoint", "market_price"):
            continue  # handled by the regex above with its negative-reminder carve-out
        if re.search(rf"\b{re.escape(key)}\s*[:=]", lowered):
            raise ValueError(f"blind prompt leaked a forbidden field: {key!r}")


def prompt_hash(system: str, user: str) -> str:
    return hashlib.sha256((system + "\n\n" + user).encode("utf-8")).hexdigest()
