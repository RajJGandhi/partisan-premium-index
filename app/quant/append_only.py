"""Runtime append-only guard for published Quant/Ensemble forecast rows (spec sections 3, 32).

The database's unique constraints stop a *re-run* creating a duplicate slot. This module is the
positive side of that rule: helpers that either return the existing immutable row unchanged, or
insert a brand-new one -- and a correction path that never edits history in place but links a new
revision to the original.

Nothing here mutates ``p_dem_win`` / ``expected_margin`` / ``fair_value_yes`` / any model-output
column of an already-persisted row. Only ``integrity_flag`` / ``integrity_note`` (a data-integrity
review flag that suppresses public display, never changes the number) may be set afterwards, via
:func:`flag_integrity`.
"""

from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models_quant import EnsembleForecast, QuantForecast

_IMMUTABLE_QUANT_FIELDS = (
    "polling_margin",
    "fundamental_margin",
    "poll_weight",
    "expected_margin",
    "sigma_total",
    "p_dem_win",
    "p_dem_win_uncapped",
    "p_rep_win",
    "fair_value_yes",
    "data_quality",
    "abstained",
    "input_hash",
    "config_hash",
)


class AppendOnlyViolation(RuntimeError):
    """Raised on an attempt to overwrite a persisted forecast's model-output fields."""


def existing_quant_forecast(
    session: Session, *, race_id: str, run_key: str, methodology_version: str, revision: int = 0
) -> Optional[QuantForecast]:
    return session.execute(
        select(QuantForecast).where(
            QuantForecast.race_id == race_id,
            QuantForecast.run_key == run_key,
            QuantForecast.methodology_version == methodology_version,
            QuantForecast.revision == revision,
        )
    ).scalar_one_or_none()


def upsert_quant_forecast(session: Session, row: QuantForecast) -> tuple[QuantForecast, bool]:
    """Insert *row* only if its natural-key slot is empty. Returns ``(row, created)``.

    If the slot is already populated, the persisted row is returned untouched and ``created`` is
    ``False`` -- an exact re-run is idempotent, never a silent overwrite.
    """
    if row.revision is None:
        row.revision = 0  # Python-side default applies only on flush; the lookup needs it now
    found = existing_quant_forecast(
        session,
        race_id=row.race_id,
        run_key=row.run_key,
        methodology_version=row.methodology_version,
        revision=row.revision,
    )
    if found is not None:
        return found, False
    session.add(row)
    session.flush()
    return row, True


def correct_quant_forecast(
    session: Session, original: QuantForecast, corrected: QuantForecast, *, note: str
) -> QuantForecast:
    """Append a correction: a new row (``revision = original.revision + 1``) linked to *original*.

    The original row is not modified in any way. A human/process asserting a genuine data-integrity
    problem should *also* call :func:`flag_integrity` on the original so it drops out of public
    display while remaining in history.
    """
    corrected.race_id = original.race_id
    corrected.run_key = original.run_key
    corrected.methodology_version = original.methodology_version
    corrected.revision = original.revision + 1
    corrected.correction_of_id = original.id
    corrected.integrity_note = note
    session.add(corrected)
    session.flush()
    return corrected


def flag_integrity(session: Session, row: QuantForecast | EnsembleForecast, *, note: str) -> None:
    """Set the data-integrity flag (suppresses public display). Never touches the numeric fields."""
    row.integrity_flag = "FLAGGED"
    row.integrity_note = note
    session.flush()


def assert_not_overwriting(existing: QuantForecast, incoming: QuantForecast) -> None:
    """Raise :class:`AppendOnlyViolation` if *incoming* differs from *existing* on any immutable
    model-output field (used by tests and defensive call sites)."""
    diffs = [
        f
        for f in _IMMUTABLE_QUANT_FIELDS
        if getattr(existing, f, None) != getattr(incoming, f, None)
    ]
    if diffs:
        raise AppendOnlyViolation(
            f"refusing to overwrite persisted quant_forecasts row {existing.id} "
            f"(race={existing.race_id}, run_key={existing.run_key}); differing fields: {diffs}"
        )


def existing_ensemble_forecast(
    session: Session, *, race_id: str, run_key: str, methodology_version: str, revision: int = 0
) -> Optional[EnsembleForecast]:
    return session.execute(
        select(EnsembleForecast).where(
            EnsembleForecast.race_id == race_id,
            EnsembleForecast.run_key == run_key,
            EnsembleForecast.methodology_version == methodology_version,
            EnsembleForecast.revision == revision,
        )
    ).scalar_one_or_none()


def upsert_ensemble_forecast(session: Session, row: EnsembleForecast) -> tuple[EnsembleForecast, bool]:
    if row.revision is None:
        row.revision = 0
    found = existing_ensemble_forecast(
        session,
        race_id=row.race_id,
        run_key=row.run_key,
        methodology_version=row.methodology_version,
        revision=row.revision,
    )
    if found is not None:
        return found, False
    session.add(row)
    session.flush()
    return row, True


def record_methodology_version(session: Session, *, version: str, kind: str, config: dict,
                               config_hash: str, provisional: bool = True, notes: str = "") -> None:
    """Write-once: record a methodology config snapshot the first time it is seen."""
    from app.db.models_quant import MethodologyVersion

    found = session.execute(
        select(MethodologyVersion).where(MethodologyVersion.version == version)
    ).scalar_one_or_none()
    if found is not None:
        return
    session.add(
        MethodologyVersion(
            version=version,
            kind=kind,
            config_json=json.dumps(config, sort_keys=True, separators=(",", ":")),
            config_hash=config_hash,
            provisional=provisional,
            notes=notes,
        )
    )
    session.flush()
