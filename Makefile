.PHONY: setup init seed demo run daily export-public scheduler test lint typecheck build validate web-install web-dev web-data-check web-check web-build

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
	ruff check app/ppi app/dashboard app/db/models.py app/db/database.py scripts/run_ppi_daily.py scripts/run_scheduler.py scripts/create_admin_user.py scripts/hash_password.py scripts/seed_production_markets.py scripts/backfill_polymarket_prices.py scripts/migrate_db.py scripts/run_shadow_experiment.py tests/test_ppi_*.py tests/test_evidence_*.py tests/test_snapshot_idempotency.py tests/test_stale_data.py tests/test_polymarket_integration_mock.py tests/test_publication_flow.py tests/test_streamlit_smoke.py tests/test_daily_digest.py tests/test_source_security.py tests/test_seed_data.py tests/test_database_url.py scripts/export_public_bundle.py tests/test_public_export.py tests/test_blind_forecast.py tests/test_llm_forecast_review.py tests/test_llm_forecast_view.py tests/test_strict_llm_pipeline.py tests/test_pipeline_lock.py tests/test_run_classification.py tests/test_scheduling_and_retry.py tests/test_openrouter_provider.py tests/test_migrate_db.py tests/test_run_shadow_experiment.py tests/test_dual_series_pipeline.py tests/test_experiment_metadata.py

typecheck:
	mypy app/ppi app/db/models.py app/db/database.py scripts/run_ppi_daily.py scripts/run_scheduler.py scripts/create_admin_user.py scripts/seed_production_markets.py scripts/migrate_db.py scripts/export_public_bundle.py

build:
	python -m compileall -q app scripts

validate: lint typecheck test build

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
