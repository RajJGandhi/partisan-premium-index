#!/usr/bin/env bash
# Quick health check before trusting the self-hosted runner to execute the canonical pipeline.
# Run manually any time, or before a scheduled window if you're debugging a missed run.
set -uo pipefail

RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
MODEL="${LLM_MODEL:-qwen3:8b}"
FAILED=0

check() {
  local description="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "OK   $description"
  else
    echo "FAIL $description"
    FAILED=1
  fi
}

echo "== Ollama =="
check "ollama binary on PATH" command -v ollama
check "Ollama API reachable at http://127.0.0.1:11434" curl -sf --max-time 5 http://127.0.0.1:11434/api/tags
if curl -s --max-time 5 http://127.0.0.1:11434/api/tags 2>/dev/null | grep -q "$MODEL"; then
  echo "OK   $MODEL is pulled"
else
  echo "FAIL $MODEL is not pulled -- run: ollama pull $MODEL"
  FAILED=1
fi

if [ -n "${OLLAMA_HOST:-}" ]; then
  if [[ "$OLLAMA_HOST" == "127.0.0.1"* ]] || [[ "$OLLAMA_HOST" == "localhost"* ]]; then
    echo "OK   OLLAMA_HOST='$OLLAMA_HOST' is localhost-only"
  else
    echo "FAIL OLLAMA_HOST='$OLLAMA_HOST' is NOT localhost-only -- Ollama may be network-exposed"
    FAILED=1
  fi
else
  echo "OK   OLLAMA_HOST unset (defaults to 127.0.0.1 only)"
fi

echo ""
echo "== Keep-awake =="
if pgrep -f "caffeinate -dis" >/dev/null 2>&1; then
  echo "OK   caffeinate -dis is running"
else
  echo "FAIL caffeinate -dis is not running -- the Mac may sleep and miss scheduled runs"
  FAILED=1
fi

echo ""
echo "== GitHub Actions runner =="
if [ -x "$RUNNER_DIR/svc.sh" ]; then
  if (cd "$RUNNER_DIR" && ./svc.sh status) 2>&1 | grep -qi "active (running)\|started"; then
    echo "OK   runner service is running"
  else
    echo "FAIL runner service is not running -- cd $RUNNER_DIR && ./svc.sh start"
    FAILED=1
  fi
else
  echo "FAIL runner not installed at $RUNNER_DIR -- see ops/self-hosted-runner/install.sh"
  FAILED=1
fi

echo ""
if [ "$FAILED" -eq 0 ]; then
  echo "All checks passed."
else
  echo "One or more checks failed -- see docs/SELF_HOSTED_RUNNER.md for troubleshooting."
fi
exit "$FAILED"
