#!/usr/bin/env bash
# Starts the runner service and the keep-awake agent. Assumes install.sh has already been run
# and the runner has already been registered (./config.sh) at least once.
set -euo pipefail

RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
PLIST="$HOME/Library/LaunchAgents/com.ppi.keepawake.plist"

if [ -f "$PLIST" ]; then
  launchctl load "$PLIST" 2>/dev/null || echo "Keep-awake agent already loaded."
else
  echo "Keep-awake agent not installed -- run ops/self-hosted-runner/install.sh first." >&2
  exit 1
fi

if [ -x "$RUNNER_DIR/svc.sh" ]; then
  (cd "$RUNNER_DIR" && ./svc.sh start)
else
  echo "Runner not installed at $RUNNER_DIR -- run ops/self-hosted-runner/install.sh first." >&2
  exit 1
fi

echo "Started. Run ops/self-hosted-runner/preflight.sh to verify."
