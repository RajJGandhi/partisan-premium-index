#!/usr/bin/env bash
# Reverses install.sh: stops and unregisters the runner service, unloads the keep-awake agent.
# Does NOT delete the repository, the database, or any pipeline data -- only this machine's
# runner registration and the two launchd agents it introduced.
set -euo pipefail

RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

echo "== Stopping and uninstalling the runner service =="
if [ -x "$RUNNER_DIR/svc.sh" ]; then
  (cd "$RUNNER_DIR" && ./svc.sh stop) || true
  (cd "$RUNNER_DIR" && ./svc.sh uninstall) || true
else
  echo "No runner service found at $RUNNER_DIR/svc.sh -- skipping."
fi

echo "== Unloading the keep-awake agent =="
PLIST="$LAUNCH_AGENTS_DIR/com.ppi.keepawake.plist"
if [ -f "$PLIST" ]; then
  launchctl unload "$PLIST" || true
  rm -f "$PLIST"
  echo "Removed $PLIST"
else
  echo "No keep-awake agent found -- skipping."
fi

cat <<EOF

== Manual step remaining ==

Remove the runner's GitHub registration (needs a removal token from the same Settings page used
to register it):
  cd "$RUNNER_DIR"
  ./config.sh remove --token <REMOVAL_TOKEN>

The scheduled workflow will simply queue with no runner available until you either re-register
this machine or disable scheduling -- see docs/SELF_HOSTED_RUNNER.md "How to disable scheduling
safely" if that's actually what you want.
EOF
