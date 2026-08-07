---
paths:
  - "app/**/*.py"
  - "scripts/**/*.py"
  - "tests/**/*.py"
  - "migrations/**/*.sql"
---

# Python and data rules

- Target Python 3.11 until the repository is deliberately upgraded end to end.
- Use type hints for public functions and nontrivial internal boundaries.
- Prefer small pure functions for calculations and explicit service/orchestration boundaries for I/O.
- Use timezone-aware UTC datetimes. Never mix naive and aware timestamps.
- Use SQLAlchemy sessions and migrations consistently; do not patch production schemas ad hoc.
- Design scheduled writes around stable run keys, unique constraints, and safe retries.
- Use explicit HTTP timeouts, bounded retries, useful user agents, and normal TLS verification.
- Do not catch broad exceptions unless recording context and re-raising or converting to an explicit failed/partial state.
- Validate external payloads before persistence and retain the raw payload separately.
- Do not rely on unordered API responses or local wall-clock timing in calculations.
- Keep configuration in environment/settings objects, not hardcoded secrets or machine paths.
- Use `pytest`, `ruff`, and `mypy` through the repository commands.
