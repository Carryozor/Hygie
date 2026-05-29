#!/usr/bin/env bash
# Build and start Hygie from local sources for testing.
# Usage: ./build-local.sh [--no-cache]
set -e

COMPOSE_FILE="docker-compose.local.yml"
NO_CACHE=""

if [[ "$1" == "--no-cache" ]]; then
  NO_CACHE="--no-cache"
fi

echo "==> Building hygie:local..."
docker compose -f "$COMPOSE_FILE" build $NO_CACHE

echo "==> Restarting container..."
docker compose -f "$COMPOSE_FILE" up -d --force-recreate

echo ""
echo "✓ Running at http://localhost:8000"
echo "  Logs : docker compose -f $COMPOSE_FILE logs -f"
echo "  Stop : docker compose -f $COMPOSE_FILE down"
