#!/usr/bin/env bash
# VISION-SYSTEM WEB - Genera CA local + certificado servidor self-signed.
#
# La camara (getUserMedia) exige secure context (https://). Con un certificado
# self-signed firmado por una CA local, Edge/Chrome/Firefox/Safari aceptan la
# conexion; instalar docker/certs/ca.crt en el almacén de confianza del SO
# elimina el aviso de "conexion no segura" (recomendado en kiosk/estacion).
#
# Uso:
#   bash scripts/gen-certs.sh
# Variables de entorno:
#   VISION_CERT_DIR     salida (default: <repo>/docker/certs)
#   VISION_CERT_DAYS    validez en dias (default: 3650 = 10 anos)
#   VISION_CERT_HOSTS   SANs extra separadas por coma (IP o hostname)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${VISION_CERT_DIR:-$REPO_ROOT/docker/certs}"
DAYS="${VISION_CERT_DAYS:-3650}"
EXTRA_HOSTS="${VISION_CERT_HOSTS:-}"

mkdir -p "$OUT_DIR"
cd "$OUT_DIR"

DNS_ENTRIES=()
IP_ENTRIES=()

add_dns() { DNS_ENTRIES+=("$1"); }
add_ip()  { IP_ENTRIES+=("$1"); }

add_dns "localhost"
add_ip "127.0.0.1"
add_ip "::1"

HOSTNAME_VAL="$(hostname 2>/dev/null || true)"
if [ -n "$HOSTNAME_VAL" ] && [ "$HOSTNAME_VAL" != "localhost" ]; then
    add_dns "$HOSTNAME_VAL"
fi

# IPv4 locales (excluye loopback); incluye red local 10.x/192.168.x
if command -v ip >/dev/null 2>&1; then
    while read -r addr; do
        [ -n "$addr" ] && add_ip "$addr"
    done < <(ip -4 -o addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | grep -vE '^127\.' || true)
elif command -v hostname >/dev/null 2>&1; then
    for addr in $(hostname -I 2>/dev/null || true); do
        case "$addr" in 127.*) ;; *) add_ip "$addr" ;; esac
    done
fi

# SANs extra: VISION_CERT_HOSTS="192.0.2.10,station-01"
if [ -n "$EXTRA_HOSTS" ]; then
    IFS=',' read -ra EXTRAS <<< "$EXTRA_HOSTS"
    for h in "${EXTRAS[@]}"; do
        h="$(echo "$h" | xargs)"
        [ -z "$h" ] && continue
        if echo "$h" | grep -qE '^([0-9]{1,3}\.){3}[0-9]{1,3}$'; then
            add_ip "$h"
        else
            add_dns "$h"
        fi
    done
fi

SAN=""
for d in "${DNS_ENTRIES[@]}"; do SAN+="DNS:$d,"; done
for i in "${IP_ENTRIES[@]}"; do SAN+="IP:$i,"; done
SAN="${SAN%,}"

echo "[gen-certs] SAN: $SAN"
echo "[gen-certs] Salida: $OUT_DIR (validez: $DAYS dias)"

rm -f ca.key ca.crt server.key server.csr server.crt san.cnf

# --- CA local ---
openssl genrsa -out ca.key 4096 2>/dev/null
openssl req -x509 -new -nodes -key ca.key -sha256 -days "$DAYS" \
    -subj "/O=Open Source Project/OU=Development/CN=Vision System Local CA" \
    -out ca.crt

# --- Certificado servidor ---
openssl genrsa -out server.key 2048 2>/dev/null
echo "subjectAltName=$SAN" > san.cnf
openssl req -new -key server.key -subj "/O=Open Source Project/OU=Development/CN=vision-system" -out server.csr
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -days "$DAYS" -sha256 -extfile san.cnf -out server.crt 2>/dev/null

rm -f server.csr san.cnf ca.srl
chmod 600 ca.key server.key

echo "[gen-certs] OK:"
openssl x509 -in server.crt -noout -subject -dates -ext subjectAltName
echo "[gen-certs] Para eliminar el aviso en el navegador, instalar 'ca.crt' en las"
echo "[gen-certs] Autoridades de Certificacion de Raiz de Confianza del sistema."
