#!/usr/bin/env bash
# Native uv deployment wrapper: stable working directory, overlap protection,
# and unchanged process exit status. Dependencies must be synced during deploy.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

LOCK_DIR="$PROJECT_DIR/var/run.lock"
mkdir -p "$PROJECT_DIR/var"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "[$(date '+%F %T')] trend-sift is already running; skipped" >&2
    exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

if (( $# == 0 )); then
    set -- run
fi
uv run --frozen --no-sync trend-sift "$@"
