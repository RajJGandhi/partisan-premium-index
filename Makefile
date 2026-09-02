.PHONY: setup init seed demo run daily export-public scheduler test lint typecheck build validate web-install web-dev web-data-check web-check web-build quant-test quant-shadow quant-shadow-dry quant-shadow-blind ingest ingest-offline ingest-dry check-providers check-providers-probe score backtest eval-test v15-daily v15-daily-offline v15-export v15-test

setup:
	python -m venv .venv
	. .venv/bin/activate && pip install -r requirements-dev.txt

init:
	PYTHONPATH=. python scripts/migrate_db.py

seed:
	PYTHONPATH=. python scripts/seed_production_markets.py

demo:
	PYTHONPATH=. python scripts/seed_production_markets.py --demo

run:
	PYTHONPATH=. streamlit run app/dashboard/streamlit_app.py

daily:
	PYTHONPATH=. python scripts/run_ppi_daily.py --trigger manual

export-public:
	PYTHONPATH=. python scripts/export_public_bundle.py

scheduler:
	PYTHONPATH=. python scripts/run_scheduler.py

test:
	PYTHONPATH=. pytest -q

lint:
	ruff check app/ppi app/quant app/providers app/blind app/eval app/pipeline_v15 app/dashboard app/db/models.py app/db/models_quant.py app/db/database.py app/config.py scripts/run_ppi_daily.py scripts/run_scheduler.py scripts/create_admin_user.py scripts/hash_password.py scripts/seed_production_markets.py scripts/backfill_polymarket_prices.py scripts/migrate_db.py scripts/job_run_lifecycle.py scripts/run_shadow_experiment.py scripts/run_quant_shadow.py scripts/run_ingest.py scripts/run_scoring.py scripts/ppi_backtest.py scripts/run_v15_daily.py scripts/export_v15_bundle.py scripts/check_providers.py tests/test_ppi_*.py tests/test_evidence_*.py tests/test_snapshot_idempotency.py tests/test_twice_daily_history.py tests/test_stale_data.py tests/test_polymarket_integration_mock.py tests/test_publication_flow.py tests/test_streamlit_smoke.py tests/test_daily_digest.py tests/test_source_security.py tests/test_seed_data.py tests/test_database_url.py scripts/export_public_bundle.py tests/test_public_export.py tests/test_blind_forecast.py tests/test_llm_forecast_review.py tests/test_llm_forecast_view.py tests/test_strict_llm_pipeline.py tests/test_pipeline_lock.py tests/test_run_classification.py tests/test_scheduling_and_retry.py tests/test_job_run_lifecycle.py tests/test_openrouter_provider.py tests/test_migrate_db.py tests/test_run_shadow_experiment.py tests/test_dual_series_pipeline.py tests/test_experiment_metadata.py tests/test_quant_*.py tests/test_providers_*.py tests/test_blind_*.py tests/test_eval_*.py tests/test_v15_*.py tests/conftest.py

typecheck:
	mypy app/ppi app/quant app/providers app/blind app/eval app/pipeline_v15 app/db/models.py app/db/models_quant.py app/db/database.py app/config.py scripts/run_ppi_daily.py scripts/run_scheduler.py scripts/create_admin_user.py scripts/seed_production_markets.py scripts/migrate_db.py scripts/job_run_lifecycle.py scripts/export_public_bundle.py scripts/run_quant_shadow.py scripts/run_ingest.py scripts/run_scoring.py scripts/ppi_backtest.py scripts/run_v15_daily.py scripts/export_v15_bundle.py scripts/check_providers.py

build:
	python -m compileall -q app scripts

validate: lint typecheck test build

# PPI Quant v1.0 -- deterministic engine only (fast; no DB, no network).
quant-test:
	PYTHONPATH=. pytest -q tests/test_quant_*.py tests/test_providers_*.py

# Scoring / calibration / backtesting (spec sections 34-36, 47). No network.
eval-test:
	PYTHONPATH=. pytest -q tests/test_eval_*.py tests/test_blind_*.py

# Score every race that has a recorded resolution + print a calibration report.
score:
	PYTHONPATH=. python scripts/run_scoring.py --resolutions data/seed/quant_example_resolutions.json --calibration

# Point-in-time backtest of PPI Quant against the seeded example cycle.
backtest:
	PYTHONPATH=. python scripts/ppi_backtest.py --cycle 2026

# The full 10-stage v1.5 pipeline (shadow). Offline chains + deterministic blind stub -- no keys.
v15-daily-offline:
	PYTHONPATH=. python scripts/run_v15_daily.py --offline --blind-stub

# Live chains + live GPT/Claude (needs OPENAI_API_KEY / ANTHROPIC_API_KEY + requirements-blind.txt).
v15-daily:
	PYTHONPATH=. python scripts/run_v15_daily.py --blind

v15-export:
	PYTHONPATH=. python scripts/export_v15_bundle.py

v15-test:
	PYTHONPATH=. pytest -q tests/test_v15_*.py tests/test_eval_*.py

# Automated political-data ingestion -> DB (spec sections 5-10, 41). Never writes market data.
ingest:
	PYTHONPATH=. python scripts/run_ingest.py

ingest-offline:
	PYTHONPATH=. python scripts/run_ingest.py --offline

ingest-dry:
	PYTHONPATH=. python scripts/run_ingest.py --offline --dry-run

# Inventory the data-acquisition providers: name, endpoint family, enabled?, gating env var.
check-providers:
	PYTHONPATH=. python scripts/check_providers.py

# ... and make one live reachability request per enabled provider (needs network + any keys).
check-providers-probe:
	PYTHONPATH=. python scripts/check_providers.py --probe

# Run PPI Quant in shadow mode against the seeded example races (writes quant_forecasts /
# ensemble_forecasts only; never touches the headline llm_forecasts series or the public export).
quant-shadow:
	PYTHONPATH=. python scripts/run_quant_shadow.py

quant-shadow-dry:
	PYTHONPATH=. python scripts/run_quant_shadow.py --dry-run

# Full v1.5 shadow pass: Quant -> evidence bundle -> GPT/Claude blind benchmarks -> ensemble.
# Without OPENAI_API_KEY / ANTHROPIC_API_KEY (+ requirements-blind.txt) the blind rows are
# SKIPPED_PROVIDER and the ensemble is recorded unavailable (never reweighted to Quant alone).
quant-shadow-blind:
	PYTHONPATH=. python scripts/run_quant_shadow.py --blind

web-install:
	cd web && npm install

web-dev:
	cd web && npm run dev

web-data-check:
	cd web && npm run data:check

web-check:
	cd web && npm run check

web-build:
	cd web && npm run build
