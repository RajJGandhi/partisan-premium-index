from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.scoring.fair_value import compute_fair_value

FAIR_VALUE_COLUMNS = [
    "market_id",
    "question",
    "polymarket_slug",
    "polling_prob",
    "forecast_prob",
    "other_markets_prob",
    "expert_prob",
    "news_campaign_prob",
    "manual_fair_yes",
    "confidence",
    "source_notes",
    "last_updated",
]

MAPPING_COLUMNS = [
    "polymarket_market_id",
    "platform",
    "external_market_id",
    "external_market_url",
    "match_confidence",
    "notes",
]


def ensure_csv(path: str | Path, columns: list[str]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        pd.DataFrame(columns=columns).to_csv(p, index=False)
    return p


def _none_if_nan(value: Any) -> Any:
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def _float_or_none(value: Any) -> float | None:
    value = _none_if_nan(value)
    if value in (None, ""):
        return None
    return float(value)


def load_fair_values_csv(path: str | Path = "data/fair_values.csv") -> pd.DataFrame:
    p = ensure_csv(path, FAIR_VALUE_COLUMNS)
    return pd.read_csv(p)


def iter_fair_value_rows(path: str | Path = "data/fair_values.csv") -> list[dict[str, Any]]:
    df = load_fair_values_csv(path)
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        data = {col: _none_if_nan(row.get(col)) for col in FAIR_VALUE_COLUMNS}
        numeric = {
            "polling_prob": _float_or_none(data.get("polling_prob")),
            "forecast_prob": _float_or_none(data.get("forecast_prob")),
            "other_markets_prob": _float_or_none(data.get("other_markets_prob")),
            "expert_prob": _float_or_none(data.get("expert_prob")),
            "news_campaign_prob": _float_or_none(data.get("news_campaign_prob")),
            "manual_fair_yes": _float_or_none(data.get("manual_fair_yes")),
            "confidence": _float_or_none(data.get("confidence")),
        }
        computed = compute_fair_value(**numeric)
        data.update(numeric)
        data["computed_fair_yes"] = computed.fair_yes
        data["confidence"] = computed.adjusted_confidence
        rows.append(data)
    return rows


def load_market_mappings_csv(path: str | Path = "data/market_mappings.csv") -> pd.DataFrame:
    p = ensure_csv(path, MAPPING_COLUMNS)
    return pd.read_csv(p)
