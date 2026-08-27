"""Race-outcome ingestion (spec section 34).

A resolution is the immutable ground truth used to score every forecasting series. Written once
per race; a genuine correction is a deliberate, logged operation (there is no silent overwrite).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models_quant import ForecastResolution


def record_resolution(
    session: Session,
    race_id: str,
    *,
    dem_won: float | bool,
    final_margin_dem: Optional[float] = None,
    source_url: Optional[str] = None,
    notes: Optional[str] = None,
    allow_correction: bool = False,
) -> tuple[ForecastResolution, bool]:
    """Insert the outcome for ``race_id``. Returns ``(row, created)``.

    An existing resolution is returned unchanged unless ``allow_correction=True`` (which updates it
    in place and stamps the note) -- callers must pass that explicitly and log why.
    """
    existing = session.execute(
        select(ForecastResolution).where(ForecastResolution.race_id == race_id)
    ).scalar_one_or_none()
    y = 1.0 if (dem_won is True or float(dem_won) >= 0.5) else 0.0
    if existing is not None:
        if not allow_correction:
            return existing, False
        existing.dem_won = y
        existing.final_margin_dem = final_margin_dem
        existing.source_url = source_url
        existing.notes = f"[corrected {datetime.now(timezone.utc).isoformat()}] {notes or ''}".strip()
        session.flush()
        return existing, False
    row = ForecastResolution(
        race_id=race_id,
        dem_won=y,
        final_margin_dem=final_margin_dem,
        resolved_at=datetime.now(timezone.utc),
        source_url=source_url,
        notes=notes,
    )
    session.add(row)
    session.flush()
    return row, True


def load_resolutions_file(session: Session, path: Path) -> int:
    """Load a JSON file: ``{"resolutions": [{race_id, dem_won, final_margin_dem?, source_url?, notes?}]}``."""
    doc = json.loads(Path(path).read_text())
    n = 0
    for r in doc.get("resolutions", []):
        _row, created = record_resolution(
            session,
            r["race_id"],
            dem_won=r["dem_won"],
            final_margin_dem=r.get("final_margin_dem"),
            source_url=r.get("source_url"),
            notes=r.get("notes"),
        )
        n += int(created)
    session.flush()
    return n
