#!/usr/bin/env bash
# scripts/build-frontend.sh
# Build the Vue 3 frontend with Vite.
# Always runs from the repo root regardless of working directory.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend/vue"

echo "→ Building frontend from $FRONTEND_DIR"
cd "$FRONTEND_DIR"
npm ci --prefer-offline 2>/dev/null || npm install
npm run build
echo "✅ Frontend built → $REPO_ROOT/frontend/dist/"
