# Self-hosted GitHub Actions runner (canonical PPI pipeline)

The canonical blind-Qwen series requires a live Ollama instance for every evidence
classification and every market forecast (`--strict-llm-only`, see `PPI_ARCHITECTURE.md`). A
GitHub-hosted runner cannot reach an Ollama instance running on your Mac, so the scheduled
pipeline runs on a **self-hosted runner registered on this Mac**, twice daily at 06:00 and 18:00
America/Toronto.

Scripts referenced below live in `ops/self-hosted-runner/`.

## 1. Installation

Prerequisites:

- macOS with [Ollama](https://ollama.com) installed and `qwen3:8b` pulled:
  ```bash
  ollama pull qwen3:8b
  ```
  Ollama.app normally starts `ollama serve` automatically at login and binds to `127.0.0.1:11434`
  only. Do not set `OLLAMA_HOST` to anything other than `127.0.0.1`/`localhost` — see "Do not
  expose Ollama publicly" below.
- Admin access to the GitHub repository (Settings → Actions → Runners) to generate a
  registration token.
- Python 3.11, Node 22 available (or let the workflow's `setup-python`/`setup-node` actions
  install them into the runner's tool cache — this works the same on self-hosted runners).

Run the automated part:

```bash
cd ops/self-hosted-runner
./install.sh
```

This downloads the GitHub Actions runner package, installs the keep-awake launchd agent
(`com.ppi.keepawake.plist`), and prints the exact interactive commands for the one step that
requires your own credentials:

```bash
cd ~/actions-runner
./config.sh --url https://github.com/<owner>/<repo> \
  --token <REGISTRATION_TOKEN> \
  --name "ppi-mac" \
  --labels self-hosted,macOS,ppi \
  --work _work

./svc.sh install
./svc.sh start
```

The `ppi` label is what `.github/workflows/ppi-daily.yml` targets (`runs-on: [self-hosted,
macOS, ppi]`) — a runner without this label will never pick up the scheduled workflow, which is
a deliberate safety property: an unlabelled self-hosted runner elsewhere can't accidentally
execute this pipeline.

`./svc.sh install` registers the runner as a **launchd LaunchAgent** (GitHub's own supported
mechanism, not something this repo reimplements) so it restarts automatically after a crash or
reboot.

## 2. Required environment variables and secrets

Set on this Mac (e.g. in the runner's own environment, or `~/.actions-runner-env` sourced by
your shell profile — the runner inherits your normal user environment):

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER=ollama` | Set directly in the workflow's `env:` block already; no action needed. |
| `LLM_MODEL=qwen3:8b` | Also set in the workflow. Override only if you deliberately change the canonical model (this starts a new, separately-labelled series — see the research-integrity rule in `CLAUDE.md`). |

Set as **GitHub repository secrets** (Settings → Secrets and variables → Actions → New repository
secret) — these flow to the self-hosted runner through the normal `secrets.` context exactly like
a hosted runner, no local copy needed:

| Secret | Purpose |
|---|---|
| `DATABASE_URL` | Production database connection string. |
| `CLOUDFLARE_API_TOKEN` | Deploys the public site via `wrangler`. |
| `CLOUDFLARE_ACCOUNT_ID` | Same. |

Never put secrets in the launchd plists or in `ops/self-hosted-runner/` — those files are
checked into the repository.

## 3. Launch and shutdown

```bash
# Start the runner service + keep-awake agent
ops/self-hosted-runner/start.sh

# Stop both (safe, reversible -- does not unregister the runner)
ops/self-hosted-runner/stop.sh

# Check everything is healthy
ops/self-hosted-runner/preflight.sh
```

`start.sh`/`stop.sh` only control whether this machine is listening for scheduled work; they
never touch the runner's registration or the pipeline's database.

## 4. Keeping the Mac awake

`com.ppi.keepawake.plist` runs `caffeinate -dis` continuously (prevents display, idle, and system
sleep while on AC power) as a launchd agent with `RunAtLoad` + `KeepAlive`, independent of the
runner's own service. It's installed by `install.sh` and controlled by `start.sh`/`stop.sh`.
Logs: `~/Library/Logs/ppi/keepawake.log`.

This does **not** override explicit lid-close sleep on a laptop — if this Mac is a laptop, keep
the lid open or use `sudo pmset -c disablesleep 1` (system-wide, affects the whole machine, so
only do this if you're comfortable with that tradeoff) or run on a Mac mini/Studio that's always
plugged in with the lid concern moot.

## 5. Recovery after reboot

Both launchd agents (`com.ppi.keepawake` and the runner's own `actions.runner.*` service, via
`svc.sh install`) are registered with `RunAtLoad`, so a full reboot brings both back automatically
once the user logs in (LaunchAgents run in the user session, not before login — if you need the
runner available before any user logs in, install the runner as a LaunchDaemon instead; see
GitHub's runner docs for `svc.sh install` root-level options). After a reboot:

```bash
ops/self-hosted-runner/preflight.sh
```

If Ollama didn't restart automatically, open Ollama.app once (it registers its own login item) or
run `ollama serve &` manually.

## 6. Ollama / model checks

```bash
ops/self-hosted-runner/preflight.sh
```

checks, in order: `ollama` on PATH, the API reachable at `http://127.0.0.1:11434`, `qwen3:8b`
present, `OLLAMA_HOST` is unset or localhost-only, `caffeinate -dis` running, and the runner
service active. The scheduled workflow runs the same three Ollama checks itself as an explicit
step ("Verify Ollama is reachable, local-only, and has the model") before ever calling the
pipeline — a missing model or an exposed `OLLAMA_HOST` fails the job immediately rather than
silently producing `SKIPPED_PROVIDER` or `ollama_failed` results.

## 7. Manual retry procedure

A failed **primary** run may be retried without waiting for the backup window. The backup slot
always remains its own distinct observation — retrying primary never substitutes for or cancels
backup, and vice versa, because each has its own deterministic `run_key`
(`ppi-daily:<date>:primary` / `...:backup`) that the pipeline's idempotency logic keys on.

```bash
# Retry today's primary run (reuses the same run_key; already-OK forecasts are returned
# unchanged, nothing is duplicated -- see app/ppi/blind_forecast.py's immutability guarantee)
gh workflow run ppi-daily.yml -f slot=primary -f force=true

# Retry today's backup run
gh workflow run ppi-daily.yml -f slot=backup -f force=true

# Run an ad hoc verification pass outside the schedule (never labelled canonical -- see
# app/ppi/run_classification.py)
gh workflow run ppi-daily.yml -f slot=adhoc -f force=false
```

Or locally, directly on the runner Mac (useful for debugging before trusting a workflow run):

```bash
LLM_PROVIDER=ollama PYTHONPATH=. python scripts/run_ppi_daily.py \
  --trigger primary --strict-llm-only --force
```

`--force` is always safe: it resets that run's *counters* and lets already-`OK` forecasts,
snapshots, evidence classifications, and the blind index row stand untouched (see
`app/ppi/pipeline.py`, `app/ppi/blind_forecast.py`, `app/ppi/run_classification.py`). It never
overwrites an immutable result — it only allows genuinely incomplete/failed pieces to be retried.

## 8. How to disable scheduling safely

Three options, in order of how much you're disabling:

**Pause this machine only** (scheduled runs will queue on GitHub and wait, or time out per the
workflow's 45-minute limit — nothing is lost, just delayed):
```bash
ops/self-hosted-runner/stop.sh
```

**Disable the workflow entirely** (no runs, scheduled or manual, until re-enabled — safe, GitHub
remembers the workflow definition):
```bash
gh workflow disable ppi-daily.yml
# ... later ...
gh workflow enable ppi-daily.yml
```

**Fully remove this runner's registration** (only if decommissioning this Mac; other runners with
the `ppi` label, if any, would still pick up scheduled runs):
```bash
ops/self-hosted-runner/uninstall.sh
```

Do not delete `.github/workflows/ppi-daily.yml` as a way to "pause" — that's a repository change
that needs review like any other; use `gh workflow disable` instead, which is instant and
reversible by anyone with write access.

## Design notes

- **DST-safe scheduling**: GitHub Actions `schedule:` cron is UTC-only and does not observe
  DST. The workflow declares four cron entries (EDT- and EST-equivalent UTC hours for both
  06:00 and 18:00 Toronto) and its "Determine run slot" step checks the *real* Toronto local
  time before proceeding — a cron firing at the wrong seasonal offset is a clean no-op, not a
  duplicate or a mis-timed run. Nothing is ever fabricated; the check only decides whether to
  proceed, using the machine's actual current time.
- **Overlap prevention**: the GitHub Actions `concurrency: {group: ppi-production,
  cancel-in-progress: false}` queues rather than overlaps workflow runs; `app/ppi/lock.py`
  is a filesystem-level backstop (PID-verified, stale-lock-reclaiming) for any out-of-band
  invocation that bypasses the Actions concurrency group entirely (e.g. a manual local run
  racing a scheduled one).
- **Idempotent retries**: `run_key = ppi-daily:<date>:<slot>` is fully deterministic from the
  date and slot alone. Rerunning the same slot with `--force` reuses the same `JobRun` row and
  never creates a duplicate `LLMForecast`, `MarketSnapshot`, `EvidenceItem`, or
  `BlindIndexRun` — see `tests/test_strict_llm_pipeline.py`, `tests/test_blind_forecast.py`.
- **Publication**: only runs classified `canonical` (strict mode, zero contamination, a real
  primary/backup slot, not superseded) are ever exported as the public "latest run" — see
  `app/ppi/run_classification.py` and `scripts/export_public_bundle.py`.
