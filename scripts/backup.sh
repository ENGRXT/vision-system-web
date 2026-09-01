#!/usr/bin/env bash
# Backup: base de datos PostgreSQL + evidencia de inspecciones.
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-$(dirname "$0")/../docker/docker-compose.yml}"
BACKUP_DIR="${BACKUP_DIR:-$(dirname "$0")/../backups}"
STAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"
log() { echo -e "\033[1;32m[backup]\033[0m $*"; }

log "Dump de PostgreSQL (pg_dump)..."
docker compose -f "$COMPOSE_FILE" exec -T postgres \
    pg_dump -U vision vision_system > "$BACKUP_DIR/vision_system_${STAMP}.sql"

log "Compactando evidencia..."
docker compose -f "$COMPOSE_FILE" run --rm --entrypoint tar api \
    -czf - -C /app evidence \
    > "$BACKUP_DIR/evidence_${STAMP}.tar.gz"

log "Reteniendo los ultimos 14 backups..."
ls -1t "$BACKUP_DIR"/vision_system_*.sql 2>/dev/null | tail -n +15 | xargs -r rm --
ls -1t "$BACKUP_DIR"/evidence_*.tar.gz 2>/dev/null | tail -n +15 | xargs -r rm --

log "Backup completado en $BACKUP_DIR (stampa $STAMP)"
