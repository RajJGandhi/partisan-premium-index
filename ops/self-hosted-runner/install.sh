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

# This block (and the leftover /Users/runner/hostedtoolcache check just below) documents a
# problem that no longer needs solving on this machine: actions/setup-python's macOS Python
# builds are compiled against, and hardcode, /Users/runner/hostedtoolcache, and its installer
# unconditionally shells out to `sudo installer -pkg ... -target /` for any version not already
# registered there -- which always fails non-interactively on a self-hosted runner (no TTY, no
# password prompt possible). ppi-daily.yml's self-hosted job no longer uses actions/setup-python
# at all; see the uv step below and docs/SELF_HOSTED_RUNNER.md "Python on the self-hosted macOS
# runner" for the replacement. The hostedtoolcache check is left in place only because it's
# already correct and harmless -- it costs nothing and nothing currently depends on removing it.
TOOL_CACHE_DIR="/Users/runner/hostedtoolcache"
if [ -d "$TOOL_CACHE_DIR" ] && [ -w "$TOOL_CACHE_DIR" ]; then
  echo "OK: $TOOL_CACHE_DIR exists and is writable by $(whoami) (unused by this workflow, harmless)."
else
  echo "NOTE: $TOOL_CACHE_DIR is missing or unwritable -- this is fine, it is no longer used." >&2
fi

# uv manages a pinned, relocatable, user-space Python build (python-build-standalone) that needs
# no elevated privileges to install or run -- this is what ppi-daily.yml's self-hosted job uses
# instead of actions/setup-python. Install once, in user space, no sudo, no prompts.
UV_BIN="$HOME/.local/bin/uv"
if [ ! -x "$UV_BIN" ]; then
  echo "Installing uv (user-space, no sudo)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
"$UV_BIN" --version
echo "OK: uv is installed at $UV_BIN."

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
