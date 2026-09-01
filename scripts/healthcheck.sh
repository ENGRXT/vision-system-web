#!/usr/bin/env bash
# Healthcheck rapido: endpoints + estado de contenedores.
set -uo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
FAIL=0
log() { echo -e "\033[1;35m[health]\033[0m $*"; }

log "GET $BASE_URL/health"
curl -fsS --max-time 5 "$BASE_URL/health" && echo " OK" || { echo " FAIL"; FAIL=1; }

log "GET $BASE_URL/health/live"
curl -fsS --max-time 5 "$BASE_URL/health/live" && echo " OK" || { echo " FAIL"; FAIL=1; }

log "GET $BASE_URL/health/ready"
READY=$(curl -fsS --max-time 30 "$BASE_URL/health/ready" 2>/dev/null || echo "{}")
echo " $READY"
echo "$READY" | grep -q '"status": *"ready"' || { echo " (degraded)"; FAIL=1; }

log "Contenedores:"
if command -v docker >/dev/null 2>&1; then
    docker ps --filter "name=vision_" --format "  {{.Names}}\t{{.Status}}" || true
fi

exit $FAIL
