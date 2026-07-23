# Hermes Research Agent v0.3

This patch makes Hermes Research cleaner and less gullible.

## Improvements

v0.3:
- Adds false-positive guardrails for entity/role collisions
- Penalizes foreign-leader visit stories for `Next Prime Minister of ...` markets
- Requires PM/leadership markets to have election, polling, party, coalition, government, or leadership context before being marked strong

v0.2:
- Stable filenames based on `target_id`
- `index.md` in each daily briefing folder
- Source errors moved to separate CSV
- Relevance buckets: `strong`, `broad`, `weak`
- Weak hits retained for audit, but clearly separated
- `--disable-gdelt` flag to avoid GDELT 429 spam
- Safer briefing format for evidence use

## Copy in

```bash
cd /Users/raj/PycharmProjects/Reality_Spread/reality-spread
unzip -o ~/Downloads/reality_spread_research_hermes_v0_3.zip -d .
```

## Recommended Week 1 run

```bash
PYTHONPATH=. python scripts/run_hermes_research_agent.py   --scope parents   --days 7   --max-results-per-query 6   --disable-gdelt   --print-summary
```

## Inspect output

```bash
cat data/research/daily/$(date -u +%F)/parent_briefings/index.md
```

Open a brief:

```bash
python - <<'PY'
from pathlib import Path
import datetime as dt
d = Path("data/research/daily") / dt.datetime.utcnow().date().isoformat() / "parent_briefings"
for p in sorted(d.glob("*.md")):
    if p.name != "index.md":
        print(p)
        print(p.read_text(encoding="utf-8")[:2500])
        break
PY
```

## With GDELT enabled, slower

```bash
PYTHONPATH=. python scripts/run_hermes_research_agent.py   --scope parents   --days 7   --max-results-per-query 6   --gdelt-sleep-seconds 2.0   --print-summary
```

## Evidence overlay

Only use once brief quality looks good:

```bash
PYTHONPATH=. python scripts/run_hermes_research_agent.py   --scope parents   --days 7   --max-results-per-query 6   --disable-gdelt   --write-evidence-overlay   --print-summary
```
