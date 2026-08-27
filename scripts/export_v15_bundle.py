"""Export the sanitized PPI v1.5 public data bundle for the web app (spec sections 37-40).

Writes ``web/public/v15/`` -- additive, never touches the existing headline export
(``scripts/export_public_bundle.py``). The browser reads only these static JSON files.

    races.json            summary row per race (homepage / list, sorted by market-model spread)
    race/<race_id>.json   full breakdown (quant math, polling inputs + weights, blind rationales,
                          uncertainty components, evidence bundle, history, resolved scores)
    provider-status.json  provider_health + adapter capabilities + cutover readiness + last job run
    calibration.json      the calibration report (per-series Brier at each horizon, N always shown)
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db.database import get_session
from app.db.models import JobRun
from app.db.models_quant import (
    BlindBenchmarkForecast,
    EnsembleForecast,
    ForecastMarketComparison,
    ForecastResolution,
    ForecastScore,
    ProviderHealth,
    QuantEvidenceBundle,
    QuantForecast,
    Race,
    RaceNewsItem,
)
from app.pipeline_v15.cutover import cutover_readiness, headline_series
from app.quant.adapters import adapter_capabilities

SCHEMA_VERSION = "v15-1.0"
DEFAULT_OUTPUT = Path("web/public/data/v15")

_SECRET_MARKERS = ("api_key", "apikey", "secret", "password", "token", "authorization", "database_url", "sk-")


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _r(v: float | None, n: int = 4) -> float | None:
    return None if v is None else round(float(v), n)


def assert_safe(obj: Any, path: str = "root") -> None:
    if isinstance(obj, dict):
        for k, val in obj.items():
            if isinstance(k, str) and any(m in k.lower() for m in _SECRET_MARKERS):
                raise ValueError(f"refusing to export a secret-looking key at {path}.{k}")
            assert_safe(val, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, val in enumerate(obj):
            assert_safe(val, f"{path}[{i}]")
    elif isinstance(obj, str) and obj.lower().startswith("sk-"):
        raise ValueError(f"refusing to export an API-key-looking string at {path}")


def _latest(rows, key=lambda r: r.generated_at):
    return max(rows, key=key) if rows else None


def build_v15_bundle(session, *, generated_at: datetime | None = None) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    races = session.execute(select(Race).order_by(Race.cycle, Race.state, Race.office)).scalars().all()

    quant_by_race: dict[str, list[QuantForecast]] = defaultdict(list)
    for q in session.execute(select(QuantForecast)).scalars():
        quant_by_race[q.race_id].append(q)
    blind_by_race: dict[str, list[BlindBenchmarkForecast]] = defaultdict(list)
    for b in session.execute(select(BlindBenchmarkForecast)).scalars():
        blind_by_race[b.race_id].append(b)
    ens_by_race: dict[str, list[EnsembleForecast]] = defaultdict(list)
    for e in session.execute(select(EnsembleForecast)).scalars():
        ens_by_race[e.race_id].append(e)
    cmp_by_race: dict[str, list[ForecastMarketComparison]] = defaultdict(list)
    for c in session.execute(select(ForecastMarketComparison)).scalars():
        cmp_by_race[c.race_id].append(c)
    bundles_by_race: dict[str, list[QuantEvidenceBundle]] = defaultdict(list)
    for eb in session.execute(select(QuantEvidenceBundle)).scalars():
        bundles_by_race[eb.race_id].append(eb)
    scores_by_race: dict[str, list[ForecastScore]] = defaultdict(list)
    for sc in session.execute(select(ForecastScore)).scalars():
        scores_by_race[sc.race_id].append(sc)
    resolutions = {r.race_id: r for r in session.execute(select(ForecastResolution)).scalars()}
    news_by_race: dict[str, list[RaceNewsItem]] = defaultdict(list)
    for n in session.execute(select(RaceNewsItem).where(RaceNewsItem.contamination_status == "CLEAN")).scalars():
        news_by_race[n.race_id].append(n)

    headline = headline_series()
    summaries: list[dict] = []
    details: dict[str, dict] = {}

    for race in races:
        rid = race.race_id
        q = _latest(quant_by_race.get(rid, []))
        ens = _latest([e for e in ens_by_race.get(rid, []) if e.available]) or _latest(ens_by_race.get(rid, []))
        blinds = {b.provider: b for b in sorted(blind_by_race.get(rid, []), key=lambda x: x.generated_at)}
        gpt = blinds.get("openai")
        claude = blinds.get("anthropic")
        cmp_ens = _latest([c for c in cmp_by_race.get(rid, []) if c.series == "ensemble"],
                          key=lambda c: c.observed_at)
        cmp_quant = _latest([c for c in cmp_by_race.get(rid, []) if c.series == "quant"],
                            key=lambda c: c.observed_at)
        cmp_pref = cmp_ens or cmp_quant
        resolution = resolutions.get(rid)

        dispersion = None
        if ens is not None:
            vals = [v for v in (ens.quant_probability, ens.openai_probability, ens.anthropic_probability) if v is not None]
            if len(vals) >= 2:
                mean = sum(vals) / len(vals)
                dispersion = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5

        summary = {
            "race_id": rid,
            "state": race.state,
            "office": race.office,
            "cycle": race.cycle,
            "question": f"Will the {(race.contract_yes_party or 'DEM').title()} candidate win the "
                        f"{race.state} {race.office} race in {race.cycle}?",
            "contract_yes_party": race.contract_yes_party or "DEM",
            "data_quality": q.data_quality if q else None,
            "abstained": bool(q.abstained) if q else None,
            "quant_probability": _r(q.p_dem_win) if q else None,
            "gpt_probability": _r(gpt.probability) if gpt and gpt.status == "OK" else None,
            "claude_probability": _r(claude.probability) if claude and claude.status == "OK" else None,
            "gpt_status": gpt.status if gpt else "MISSING",
            "claude_status": claude.status if claude else "MISSING",
            "ensemble_probability": _r(ens.ensemble_probability) if ens and ens.available else None,
            "ensemble_available": bool(ens.available) if ens else False,
            "ensemble_unavailable_reason": ens.unavailable_reason if ens and not ens.available else None,
            "market_probability": _r(cmp_pref.market_probability) if cmp_pref else None,
            "market_model_spread": _r(cmp_pref.market_model_spread) if cmp_pref else None,
            "abs_spread": _r(cmp_pref.abs_spread) if cmp_pref else None,
            "quote_method": cmp_pref.quote_method if cmp_pref else None,
            "robustness": cmp_ens.robustness if cmp_ens else None,
            "dispersion": _r(dispersion),
            "liquidity": _r(cmp_pref.liquidity, 0) if cmp_pref else None,
            "latest_run_key": q.run_key if q else (ens.run_key if ens else None),
            "generated_at": _iso(q.generated_at) if q else None,
            "methodology_version": q.methodology_version if q else None,
            "resolved": None if resolution is None else {
                "dem_won": float(resolution.dem_won),
                "final_margin_dem": _r(resolution.final_margin_dem, 2),
                "resolved_at": _iso(resolution.resolved_at),
            },
        }
        summaries.append(summary)

        # --- detail ---------------------------------------------------------------------------
        u = None
        polling_inputs: list[dict] = []
        fundamentals: dict | None = None
        eb = _latest(bundles_by_race.get(rid, []), key=lambda x: x.forecast_timestamp)
        if eb is not None:
            try:
                payload = json.loads(eb.payload_json)
            except (TypeError, ValueError):
                payload = {}
            pa = payload.get("polling_average") or {}
            for pp in (pa.get("per_poll_weights") or [])[:40]:
                polling_inputs.append({
                    "pollster": pp.get("pollster"),
                    "end_date": pp.get("end_date"),
                    "margin": _r(pp.get("margin"), 2),
                    "weight": _r(pp.get("weight"), 4),
                    "weight_breakdown": {k: _r(v, 3) for k, v in (pp.get("weight_breakdown") or {}).items()},
                })
            fnd = payload.get("fundamentals") or {}
            fundamentals = {
                "state_lean": _r(fnd.get("state_lean"), 2),
                "national_environment": _r(fnd.get("national_environment"), 2),
                "incumbency_adjustment": _r(fnd.get("incumbency_adjustment"), 2),
                "incumbent_party": fnd.get("incumbent_party"),
                "fundamental_margin": _r(fnd.get("fundamental_margin"), 2),
            }
        if q is not None:
            u = {
                "sigma_total": _r(q.sigma_total, 3),
                "sigma_time": _r(q.sigma_time, 3),
                "sigma_polling": _r(q.sigma_polling, 3),
                "sigma_office": _r(q.sigma_office, 3),
                "sigma_status": _r(q.sigma_status, 3),
            }

        history: list[dict] = []
        for qh in sorted(quant_by_race.get(rid, []), key=lambda x: x.generated_at):
            eh = _latest([e for e in ens_by_race.get(rid, []) if e.run_key == qh.run_key])
            history.append({
                "run_key": qh.run_key,
                "generated_at": _iso(qh.generated_at),
                "quant": _r(qh.p_dem_win),
                "ensemble": _r(eh.ensemble_probability) if eh and eh.available else None,
            })

        details[rid] = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _iso(generated_at),
            "headline_series": headline,
            **summary,
            "quant": None if q is None else {
                "polling_margin": _r(q.polling_margin, 2),
                "fundamental_margin": _r(q.fundamental_margin, 2),
                "poll_weight": _r(q.poll_weight, 3),
                "expected_margin": _r(q.expected_margin, 2),
                "p_dem_win": _r(q.p_dem_win),
                "p_dem_win_uncapped": _r(q.p_dem_win_uncapped),
                "n_eff": _r(q.n_eff, 2),
                "used_poll_count": q.used_poll_count,
                "latest_poll_date": _iso(q.latest_poll_date),
                "uncertainty": u,
                "abstain_reasons": json.loads(q.abstain_reasons_json or "[]"),
                "config_hash": q.config_hash,
            },
            "fundamentals": fundamentals,
            "polling_inputs": polling_inputs,
            "blind": [
                {
                    "provider": b.provider,
                    "model": b.model_name,
                    "status": b.status,
                    "probability": _r(b.probability),
                    "should_abstain": bool(b.should_abstain) if b.should_abstain is not None else None,
                    "rationale": b.rationale,
                    "uncertainty_drivers": json.loads(b.uncertainty_drivers_json or "[]"),
                    "is_stub": b.publication_status == "STUB",
                }
                for b in sorted(blind_by_race.get(rid, []), key=lambda x: (x.provider, x.generated_at))
            ],
            "evidence_bundle": None if eb is None else {
                "content_hash": eb.content_hash,
                "forecast_timestamp": _iso(eb.forecast_timestamp),
                "news": [
                    {"title": n.title, "url": n.url, "source_domain": n.source_domain,
                     "category": n.category, "published_at": _iso(n.published_at)}
                    for n in news_by_race.get(rid, [])[:15]
                ],
            },
            "history": history,
            "scores": sorted(
                ({"series": s.series, "horizon_days": s.horizon_days, "brier_score": _r(s.brier_score),
                  "log_loss": _r(s.log_loss), "forecast_probability": _r(s.forecast_probability),
                  "outcome": s.outcome} for s in scores_by_race.get(rid, [])),
                key=lambda x: (x["series"], -x["horizon_days"]),
            ),
        }

    provider_status = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(generated_at),
        "providers": sorted(
            ({"name": p.provider_name, "kind": p.provider_kind, "status": p.status,
              "is_stale": bool(p.is_stale), "consecutive_failures": p.consecutive_failures,
              "last_success_at": _iso(p.last_success_at), "last_attempt_at": _iso(p.last_attempt_at),
              "last_latency_ms": p.last_latency_ms, "latest_data_timestamp": _iso(p.latest_data_timestamp),
              "recent_error": (p.recent_error or "")[:300] or None}
             for p in session.execute(select(ProviderHealth)).scalars()),
            key=lambda x: x["name"],
        ),
        "adapters": adapter_capabilities(),
        "cutover": cutover_readiness(session),
    }
    last_job = session.execute(
        select(JobRun).where(JobRun.job_name == "ppi-v15-daily").order_by(JobRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()
    if last_job is not None:
        provider_status["latest_job_run"] = {
            "run_key": last_job.run_key, "status": last_job.status,
            "started_at": _iso(last_job.started_at), "finished_at": _iso(last_job.finished_at),
            "markets_attempted": last_job.markets_attempted, "markets_succeeded": last_job.markets_succeeded,
        }

    from app.eval.calibration import build_calibration_report

    cal = build_calibration_report(session, group_by=("series", "horizon_days"))

    return {
        "races": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _iso(generated_at),
            "headline_series": headline,
            "headline_note": "PPI v1.5 series shown in shadow -- the public headline is still the "
                             "legacy blind-LLM series (see docs/research/PPI_CUTOVER.md)"
                             if headline == "legacy_blind_llm" else f"headline series: {headline}",
            "races": summaries,
        },
        "details": details,
        "provider_status": provider_status,
        "calibration": {"schema_version": SCHEMA_VERSION, "generated_at": _iso(generated_at), **cal.as_dict()},
    }


def write_v15_bundle(bundle: dict, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "race").mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def _dump(path: Path, payload: Any) -> None:
        assert_safe(payload, str(path.name))
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n")
        written.append(path)

    _dump(output_dir / "races.json", bundle["races"])
    _dump(output_dir / "provider-status.json", bundle["provider_status"])
    _dump(output_dir / "calibration.json", bundle["calibration"])
    for rid, detail in bundle["details"].items():
        _dump(output_dir / "race" / f"{rid}.json", detail)
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description="Export the PPI v1.5 public data bundle.")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args()
    with get_session() as session:
        bundle = build_v15_bundle(session)
    written = write_v15_bundle(bundle, args.output)
    print(json.dumps({
        "output_dir": str(args.output),
        "files": len(written),
        "races": len(bundle["races"]["races"]),
        "headline_series": bundle["races"]["headline_series"],
    }, indent=2))


if __name__ == "__main__":
    main()
