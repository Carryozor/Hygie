#!/usr/bin/env bash
# scripts/deploy.sh
# Atomic deploy: build frontend, copy all backend + frontend files,
# clear pycache, restart container, verify health.
# Usage: bash scripts/deploy.sh [container_name]
set -euo pipefail

CONTAINER="${1:-hygie}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "═══════════════════════════════════════════"
echo "  Hygie Deploy → container: $CONTAINER"
echo "═══════════════════════════════════════════"

# ── 1. Build frontend ─────────────────────────────────────────────────────────
echo "→ [1/5] Building frontend…"
bash "$REPO_ROOT/scripts/build-frontend.sh"

# ── 2. Copy backend root files ────────────────────────────────────────────────
echo "→ [2/5] Copying backend files…"
for f in "$REPO_ROOT"/backend/*.py; do
    [ -f "$f" ] || continue
    fname="$(basename "$f")"
    docker cp "$f" "$CONTAINER:/app/backend/$fname" 2>/dev/null || true
done

# Copy backend subdirectories
for dir in routers arr_clients db rules; do
    if [ -d "$REPO_ROOT/backend/$dir" ]; then
        for f in "$REPO_ROOT/backend/$dir/"*.py; do
            [ -f "$f" ] || continue
            docker cp "$f" "$CONTAINER:/app/backend/$dir/$(basename "$f")" 2>/dev/null || true
        done
    fi
done

# Copy scanner package (subpackage with __init__.py)
if [ -d "$REPO_ROOT/backend/scanner" ]; then
    docker exec -u root "$CONTAINER" mkdir -p /app/backend/scanner 2>/dev/null || true
    for f in "$REPO_ROOT/backend/scanner/"*.py; do
        [ -f "$f" ] || continue
        docker cp "$f" "$CONTAINER:/app/backend/scanner/$(basename "$f")" 2>/dev/null || true
    done
fi

# ── 3. Copy frontend dist ─────────────────────────────────────────────────────
echo "→ [3/5] Copying frontend dist…"
docker cp "$REPO_ROOT/frontend/dist/." "$CONTAINER:/app/frontend/dist/"

# ── 4. Clear pycache ──────────────────────────────────────────────────────────
echo "→ [4/5] Clearing pycache…"
docker exec -u root "$CONTAINER" find /app/backend -name "*.pyc" -delete 2>/dev/null || true

# ── 5. Restart + health-check ─────────────────────────────────────────────────
echo "→ [5/5] Restarting $CONTAINER…"
docker restart "$CONTAINER" > /dev/null

echo "   Waiting for /health…"
for i in $(seq 1 15); do
    sleep 2
    if docker exec "$CONTAINER" python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" > /dev/null 2>&1; then
        echo "✅ Deploy complete — Hygie is healthy"
        exit 0
    fi
    printf "   (%d/15) still starting…\n" "$i"
done

echo "❌ Health-check failed after 30s"
echo "   Run: docker logs $CONTAINER --tail 20"
exit 1
