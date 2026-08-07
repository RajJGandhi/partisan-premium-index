#!/usr/bin/env bash
# Stops the runner service and the keep-awake agent WITHOUT unregistering the runner or removing
# any configuration -- safe, reversible pause. Re-enable with start.sh. This is the recommended
# way to temporarily disable scheduled runs; see docs/SELF_HOSTED_RUNNER.md "How to disable
# scheduling safely" for the other (also safe) options.
set -euo pipefail

RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
PLIST="$HOME/Library/LaunchAgents/com.ppi.keepawake.plist"

if [ -x "$RUNNER_DIR/svc.sh" ]; then
  (cd "$RUNNER_DIR" && ./svc.sh stop) || true
else
  echo "Runner not installed at $RUNNER_DIR -- nothing to stop."
fi

if [ -f "$PLIST" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  echo "Keep-awake agent unloaded (the Mac may sleep now)."
fi

echo "Stopped. A scheduled run that fires while stopped will simply queue until you run start.sh again."
