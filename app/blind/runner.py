"""Blind-benchmark orchestration (spec sections 23, 24, 44).

``run_blind_forecasts`` takes the timestamp-locked :class:`EvidenceBundle` and a contract question,
runs each configured provider once, and appends a ``blind_benchmark_forecasts`` row per provider.
It has **no market-price parameter** and never receives the Quant probability or the other model's
forecast -- see ``tests/test_blind_market_independence.py``.

Cost control (spec section 44): a slot whose newest row is ``OK`` with the same evidence hash,
model, and prompt version is returned unchanged (no re-call). A changed evidence hash / model /
prompt version, or a still-failed slot, appends a **new revision** (append-only; the prior row is
never edited).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blind.prompt import PROMPT_VERSION, SYSTEM_INSTRUCTIONS, build_blind_prompt, prompt_hash
from app.blind.providers import BlindForecastProvider
from app.blind.schema import BlindResponseParseError, parse_blind_response
from app.config import get_settings
from app.db.models_quant import BlindBenchmarkForecast
from app.quant.types import EvidenceBundle

STATUS_OK = "OK"
STATUS_ABSTAINED = "ABSTAINED"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED_PROVIDER"


@dataclass
class BlindRunSummary:
    race_id: str
    run_key: str
    rows: list[BlindBenchmarkForecast]
    total_tokens: int
    reused: int
    skipped: int
    failed: int

    def as_dict(self) -> dict:
        return {
            "race_id": self.race_id,
            "run_key": self.run_key,
            "providers": [
                {
                    "provider": r.provider,
                    "model": r.model_name,
                    "status": r.status,
                    "probability": r.probability,
                    "revision": r.revision,
                    "tokens": r.total_tokens,
                }
                for r in self.rows
            ],
            "total_tokens": self.total_tokens,
            "reused": self.reused,
            "skipped": self.skipped,
            "failed": self.failed,
        }


def _newest_slot_row(
    session: Session, *, race_id: str, run_key: str, provider: str, methodology_version: str
) -> Optional[BlindBenchmarkForecast]:
    return session.execute(
        select(BlindBenchmarkForecast)
        .where(
            BlindBenchmarkForecast.race_id == race_id,
            BlindBenchmarkForecast.run_key == run_key,
            BlindBenchmarkForecast.provider == provider,
            BlindBenchmarkForecast.methodology_version == methodology_version,
        )
        .order_by(BlindBenchmarkForecast.revision.desc())
        .limit(1)
    ).scalar_one_or_none()


def run_blind_forecasts(
    session: Session,
    *,
    race_id: str,
    run_key: str,
    evidence_bundle: EvidenceBundle,
    contract_question: str,
    providers: Sequence[BlindForecastProvider],
    evidence_bundle_id: int | None = None,
    job_run_id: int | None = None,
    run_slot: str | None = None,
    methodology_version: str | None = None,
    prompt_version: str | None = None,
    allow_reuse: bool = True,
    max_retries: int | None = None,
) -> BlindRunSummary:
    settings = get_settings()
    methodology_version = methodology_version or settings.blind_methodology_version
    prompt_version = prompt_version or PROMPT_VERSION
    retries_allowed = settings.blind_max_retries if max_retries is None else max_retries
    evidence_hash = evidence_bundle.content_hash
    user_prompt = build_blind_prompt(evidence_bundle, contract_question=contract_question)  # asserts market-free
    p_hash = prompt_hash(SYSTEM_INSTRUCTIONS, user_prompt)

    rows: list[BlindBenchmarkForecast] = []
    reused = skipped = failed = total_tokens = 0

    for provider in providers:
        prev = _newest_slot_row(
            session, race_id=race_id, run_key=run_key, provider=provider.provider_name,
            methodology_version=methodology_version,
        )
        if (
            allow_reuse
            and prev is not None
            and prev.status in (STATUS_OK, STATUS_ABSTAINED)
            and prev.evidence_bundle_hash == evidence_hash
            and prev.model_name == provider.model_name
            and prev.prompt_version == prompt_version
        ):
            rows.append(prev)
            reused += 1
            total_tokens += prev.total_tokens or 0
            continue

        revision = 0 if prev is None else prev.revision + 1
        correction_of_id = prev.id if prev is not None else None
        base = dict(
            race_id=race_id,
            job_run_id=job_run_id,
            run_key=run_key,
            run_slot=run_slot,
            provider=provider.provider_name,
            model_name=provider.model_name,
            prompt_version=prompt_version,
            prompt_hash=p_hash,
            methodology_version=methodology_version,
            evidence_bundle_id=evidence_bundle_id,
            evidence_bundle_hash=evidence_hash,
            evidence_cutoff=evidence_bundle.forecast_timestamp,
            contract_question=contract_question,
            generated_at=datetime.now(timezone.utc),
            revision=revision,
            correction_of_id=correction_of_id,
            pipeline_mode="shadow",
            publication_status="STUB" if getattr(provider, "is_stub", False) else "SHADOW",
        )

        if not provider.enabled():
            row = BlindBenchmarkForecast(
                **base,
                status=STATUS_SKIPPED,
                error_message="provider not enabled: missing API key or SDK not installed",
            )
            session.add(row)
            session.flush()
            rows.append(row)
            skipped += 1
            continue

        last_err: str | None = None
        call = None
        parsed = None
        attempts = 0
        for attempt in range(1, retries_allowed + 2):
            attempts = attempt
            try:
                call = provider.generate(system=SYSTEM_INSTRUCTIONS, user=user_prompt)
                parsed = parse_blind_response(call.raw_text)
                last_err = None
                break
            except BlindResponseParseError as exc:
                last_err = f"parse: {exc}"
            except Exception as exc:  # provider/network/SDK error -- record and retry, never crash
                last_err = f"{type(exc).__name__}: {exc}"
            if attempt <= retries_allowed:
                time.sleep(0)  # hook point; real backoff handled by SDK clients

        if parsed is not None and call is not None:
            status = STATUS_ABSTAINED if parsed.should_abstain else STATUS_OK
            row = BlindBenchmarkForecast(
                **base,
                status=status,
                probability=parsed.probability,
                should_abstain=parsed.should_abstain,
                rationale=parsed.rationale,
                uncertainty_drivers_json=json.dumps(parsed.uncertainty_drivers),
                base_rate_notes=parsed.base_rate_notes or None,
                model_version=call.model_version,
                raw_request_json=json.dumps(call.request_summary, default=str),
                raw_response=call.raw_text,
                prompt_tokens=call.prompt_tokens,
                completion_tokens=call.completion_tokens,
                total_tokens=call.total_tokens,
                web_search_calls=call.web_search_calls,
                retries=attempts - 1,
            )
            total_tokens += call.total_tokens or 0
        else:
            row = BlindBenchmarkForecast(
                **base,
                status=STATUS_FAILED,
                error_message=(last_err or "unknown failure")[:2000],
                raw_response=(call.raw_text if call is not None else None),
                retries=attempts - 1,
            )
            failed += 1

        session.add(row)
        session.flush()
        rows.append(row)

    return BlindRunSummary(
        race_id=race_id,
        run_key=run_key,
        rows=rows,
        total_tokens=total_tokens,
        reused=reused,
        skipped=skipped,
        failed=failed,
    )
