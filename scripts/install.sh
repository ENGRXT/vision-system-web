#!/usr/bin/env bash
# Instalacion rapida de VISION-SYSTEM WEB (requiere Docker + docker compose).
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ENGRXT/vision-system-web.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/vision-system-web}"
COMPOSE_FILE="${INSTALL_DIR}/docker/docker-compose.yml"

log() { echo -e "\033[1;34m[install]\033[0m $*"; }

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: Docker no encontrado. Instale Docker + docker compose plugin." >&2
    exit 1
fi

log "Descargando el repositorio..."
if [ ! -d "$INSTALL_DIR" ]; then
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

if [ ! -f .env ]; then
    log "Creando .env a partir de .env.example..."
    cp .env.example .env
    echo "  -> Edite .env (POSTGRES_PASSWORD, AI_BASE_URL, etc.) si es necesario."
fi

log "Construyendo e iniciando contenedores..."
docker compose -f docker/docker-compose.yml up -d --build

log "Aplicando migraciones (alembic upgrade head)..."
for i in $(seq 1 30); do
    if docker compose -f docker/docker-compose.yml exec -T api \
        alembic upgrade head 2>/dev/null; then
        break
    fi
    [ "$i" = "30" ] && { echo "ERROR: alembic no pudo migrar. Verifique logs:" >&2
                         docker compose -f docker/docker-compose.yml logs postgres api >&2
                         exit 1; }
    sleep 2
done

log "Esperando healthcheck..."
for i in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8080/health/live >/dev/null 2>&1; then
        log "OK: VISION-SYSTEM WEB disponible en http://127.0.0.1:8080"
        log "Documentacion: http://127.0.0.1:8080/docs"
        exit 0
    fi
    sleep 2
done

echo "ERROR: el servicio no responde en 60s. Verifique:" >&2
echo "  docker compose -f docker/docker-compose.yml logs" >&2
exit 1
