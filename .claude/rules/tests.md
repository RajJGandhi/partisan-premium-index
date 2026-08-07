---
paths:
  - "tests/**/*.py"
  - "app/**/*.py"
  - "scripts/**/*.py"
---

# Testing rules

- Add a regression test before fixing a reproducible bug.
- Default tests must be deterministic, offline, and safe to run repeatedly.
- Mock Polymarket, Ollama, evidence feeds, clocks, and alerts in the normal suite.
- Mark live API/model checks as explicit opt-in integration tests.
- Use temporary databases and files; never point tests at production or a developer's real database.
- Cover idempotent reruns, twice-daily uniqueness, append-only forecast history, blind prompt construction, schema validation, stale data, partial failures, exports, and scoring.
- Assert that forbidden market-price fields cannot enter model prompt payloads.
- Test both legitimate empty states and malformed upstream responses.
- Avoid sleeps and timing-sensitive assertions; inject clocks or use fixed timestamps.
