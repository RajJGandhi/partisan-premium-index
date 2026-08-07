#!/usr/bin/env bash
# Installs the GitHub Actions self-hosted runner and the keep-awake launchd agent on this Mac.
#
# This script does the parts that can be automated. Registering the runner against your GitHub
# repository requires a one-time interactive step with a token only you can generate (Settings ->
# Actions -> Runners -> New self-hosted runner), so this script stops right before that and
# prints the exact next commands. See docs/SELF_HOSTED_RUNNER.md for the full walkthrough.
set -euo pipefail

RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
RUNNER_VERSION="${RUNNER_VERSION:-2.321.0}"
LOG_DIR="${PPI_LOG_DIR:-$HOME/Library/Logs/ppi}"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/com.ppi.keepawake.plist"
PLIST_DEST="$LAUNCH_AGENTS_DIR/com.ppi.keepawake.plist"

echo "== 1. Checking prerequisites =="
for cmd in curl tar launchctl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Missing required command: $cmd" >&2; exit 1; }
done

if ! command -v ollama >/dev/null 2>&1; then
  echo "WARNING: 'ollama' is not on PATH. Install Ollama.app from https://ollama.com before continuing." >&2
fi

echo "== 2. Downloading GitHub Actions runner v${RUNNER_VERSION} =="
mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"
ARCH="$(uname -m)"
case "$ARCH" in
  arm64) RUNNER_ARCH="osx-arm64" ;;
  x86_64) RUNNER_ARCH="osx-x64" ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac
TARBALL="actions-runner-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
if [ ! -f "$TARBALL" ]; then
  curl -fsSL -o "$TARBALL" \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${TARBALL}"
fi
tar xzf "$TARBALL"

echo "== 3. Installing the keep-awake launchd agent =="
mkdir -p "$LOG_DIR"
mkdir -p "$LAUNCH_AGENTS_DIR"
sed "s|__LOG_DIR__|$LOG_DIR|g" "$PLIST_SRC" > "$PLIST_DEST"
launchctl unload "$PLIST_DEST" >/dev/null 2>&1 || true
launchctl load "$PLIST_DEST"
echo "Keep-awake agent installed and loaded (logs: $LOG_DIR/keepawake.log)."

cat <<EOF

== 4. Register the runner (manual, one-time) ==

Generate a registration token in your browser:
  https://github.com/<owner>/<repo>/settings/actions/runners/new

Then run:
  cd "$RUNNER_DIR"
  ./config.sh --url https://github.com/<owner>/<repo> \\
    --token <REGISTRATION_TOKEN> \\
    --name "ppi-mac" \\
    --labels self-hosted,macOS,ppi \\
    --work _work

== 5. Install and start the runner as a launchd service ==

  cd "$RUNNER_DIR"
  ./svc.sh install
  ./svc.sh start

Verify:
  ./svc.sh status
  launchctl list | grep -i actions.runner

Then set repository secrets (Settings -> Secrets and variables -> Actions):
  DATABASE_URL, CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID
See docs/SELF_HOSTED_RUNNER.md "Required environment variables and secrets" for the full list.
EOF
