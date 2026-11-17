# VISION-SYSTEM WEB v4.0

Sistema Web de **inspección automatizada de pines en sockets de motherboards**
(Intel LGA / AMD AM5) mediante un **pipeline híbrido**: visión artificial
determinista (OpenCV) que MIDE (conteo exacto de pines, tipo de socket,
defectos) + un VLM local que solo VERIFICA:
**Qwen3-VL-32B-Instruct-FP8** servido con **vLLM** (API OpenAI-compatible).

El operador escanea el número de serie con un escáner USB HID (emula teclado),
la cámara USB captura el socket, el sistema clasifica el estado (PASS / FAIL /
UNCERTAIN) y el sistema:

- guarda evidencia audit (`MES/DD-MM-YYYY/SERIAL_MMDDYYYY/`),
- notifica en tiempo real a todas las estaciones vía **WebSocket**,
- permite **override manual** (PASS/FAIL) con motivo y operador,
- mantiene auditoría de eventos y configuración.

## Arquitectura

```
┌────────────┐   HTTP/WS   ┌─────────────┐   /v1 (OpenAI)   ┌─────────────────┐
│  Navegador │ ──────────► │     Nginx   │ ───────────────► │ API FastAPI     │
│  (SPA JS)  │  :8080      │  (proxy)    │                  │  uvicorn :8000  │
└────────────┘             └─────────────┘                  └───────┬─────────┘
                                                                    │
                                                   ┌────────────────┼────────────────┐
                                                   ▼                ▼                ▼
                                              PostgreSQL        Evidencia        vLLM (Qwen3-VL)
                                              (inspecciones,   (volume)     http://localhost:8000/v1
                                               eventos, users)
```

**Stack:** FastAPI · SQLAlchemy 2 (async, asyncpg) · Alembic · PostgreSQL 16 ·
WebSocket nativo · Nginx · Docker Compose · Vanilla JS (ES6+) + Tailwind CDN ·
Pillow (anotación de imágenes) · OpenCV + NumPy (medición CV) · Loguru.

## Estructura

```
vision-system-web/
├── app/
│   ├── main.py                  # App FastAPI, lifespan, ensure_schema, static
│   ├── config.py                # Settings (env > config.yaml > defaults)
│   ├── database.py              # engine async + get_db
│   ├── models/                  # inspections, websocket_events, users
│   ├── schemas/                 # Pydantic (API + eventos WS)
│   ├── services/
│   │   ├── ai_proxy.py          # pipeline híbrido CV+VLM + circuit breaker
│   │   ├── image_processor.py   # base64, normalización, anotación (PIL)
│   │   ├── storage_service.py   # evidencia en disco + listado/resolve
│   │   └── websocket_manager.py # broadcast a estaciones conectadas
│   ├── utils/
│   │   ├── socket_cv.py         # medición CV: localizar, contar, detectar
│   │   └── image_ops.py         # normalización, crop, remapeo de bboxes
│   ├── routers/                 # health, inspection, evidence, config, ws
│   └── static/                  # SPA: index.html + js + css + assets
├── alembic/                     # migraciones (001_initial_schema)
├── docker/                      # Dockerfile, nginx, compose, init sql
├── scripts/                     # install.sh, backup.sh, healthcheck.sh
└── tests/                       # pytest (API + WS + IA mock + storage)
```

## Instalación rápida (Docker)

```bash
bash scripts/install.sh
# o manualmente:
git clone <REPO_URL> vision-system-web && cd vision-system-web
cp .env.example .env        # editar POSTGRES_PASSWORD, AI_BASE_URL...
docker compose -f docker/docker-compose.yml up -d --build
docker compose -f docker/docker-compose.yml exec api alembic upgrade head
```

- UI (navegador, cámara): `https://<host>:443/`
- Swagger: `https://<host>:443/docs`
- Health/API (no navegador): `http://127.0.0.1:8080/health/ready`

### Desarrollo local (sin Docker)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
# requerido: PostgreSQL local y endpoint vLLM accesibles
export DATABASE_URL=postgresql+asyncpg://vision:pass@localhost:5432/vision_system
alembic upgrade head
uvicorn app.main:app --reload
pytest
```

## Configuración

Prioridad: **variables de entorno** (`.env`) > `config.yaml` (`VISION_CONFIG`) > defaults.

| Variable | Default | Descripción |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://vision:***@postgres:5432/vision_system` | BD principal |
| `AI_BASE_URL` | `http://localhost:8000/v1` | Endpoint vLLM (OpenAI-compatible) |
| `AI_MODEL_NAME` | `qwen3-vl` | Nombre del modelo en vLLM |
| `AI_API_KEY` | `dummy` | API key (vLLM local no valida) |
| `AI_TIMEOUT_SECONDS` | `180` | Timeout de inferencia |
| `AI_MAX_RETRIES` | `2` | Reintentos con backoff exponencial |
| `STORAGE_BASE_PATH` | `/app/evidence` | Raíz de evidencia en servidor |
| `LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR |
| `POSTGRES_PASSWORD` | `CHANGE_ME_TO_A_UNIQUE_RANDOM_VALUE` | Solo compose (prod: cambiar) |
| `VISION_HTTPS_PORT` | `443` | Puerto HTTPS publicado (nginx) |

También editable **en caliente** desde la UI (panel Administración) vía
`PUT /api/config` (emite `CONFIG_UPDATED` a todas las estaciones).

## HTTPS (cámara)

`getUserMedia` (cámara web) **solo funciona en secure context**: `https://` o
`localhost`. Por eso nginx sirve dos servicios:

| Puerto | Uso |
|---|---|
| `80` (→ `8080` host) | API, health, evidencia para clientes no-navegador (curl, monitoring). La raíz `/` redirige 301 a HTTPS |
| `443` (→ `VISION_HTTPS_PORT`) | UI para navegador (Edge, Chrome, Firefox, Safari) con cámara y WSS |

El frontend elige `ws://` o `wss://` automáticamente según el protocolo de la página.

### Certificados

`install.sh` genera automáticamente una **CA local + certificado self-signed**
(`scripts/gen-certs.sh` → `docker/certs/`) con SANs automáticas: `localhost`,
hostname e IPs locales. Para un IP/hostname fijo de la estación:

```bash
VISION_CERT_HOSTS="10.145.123.50,estacion-01" bash scripts/gen-certs.sh
docker compose -f docker/docker-compose.yml restart nginx
```

Al ser self-signed, el navegador muestra un aviso de seguridad en el primer
acceso (Edge/Chrome: **Avanzado → Continuar a <host>**; Firefox: **Aceptar el
riesgo y continuar**). Para eliminarlo en la estación (recomendado en kiosk),
instale `docker/certs/ca.crt` en el almacén de confianza del SO:

- **Windows**: doble clic en `ca.crt` → *Instalar certificado* → *Local del
  equipo* → *Colocar en: Autoridades de certificación de raíz de confianza*.

## API

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/inspection` | Inspección (multipart: `image`, `serial_number`, `socket_type`, `station_id`, `operator_id`) |
| GET | `/api/inspections` | Listado con filtros (`serial_number`, `station_id`, `status`, `date_folder`, `limit`, `offset`) |
| POST | `/api/inspections/{id}/override` | Override manual PASS/FAIL con motivo |
| GET | `/api/evidence/list` | Jerarquía MES → FECHA → inspecciones |
| GET | `/api/evidence/{month}/{date}/{folder}/{file}` | Descarga de archivo de evidencia |
| GET | `/api/config` | Config actual (credenciales enmascaradas) |
| PUT | `/api/config` | Actualización en caliente |
| POST | `/api/config/test-ai-connection` | Prueba de conexión al vLLM |
| GET | `/api/ai/models` | Modelos disponibles en el vLLM |
| GET | `/health`, `/health/live`, `/health/ready` | Healthchecks |
| WS | `/ws/notifications?station_id=..&operator_id=..` | Notificaciones en tiempo real |

### Eventos WebSocket

`STATION_CONNECTED`, `STATION_DISCONNECTED`, `INSPECTION_STARTED`,
`INSPECTION_COMPLETED`, `INSPECTION_OVERRIDDEN`, `DEFECT_DETECTED`
(con `image_url` de la imagen anotada), `AI_SERVICE_DOWN`,
`AI_SERVICE_RECOVERED`, `CONFIG_UPDATED`.

Formato: `{"event": "<TIPO>", "data": {...}, "timestamp": "<ISO8601>"}`.
El cliente envía `ping` (25 s) y recibe `pong`.

## Almacenamiento de evidencia

```
/app/evidence/
└── AUGUST/
    └── 31-08-2026/
        └── SER1234_08312026/          # (colisiones: _001, _002...)
            ├── 01_original.png        # normalizada (RGB, ≤1600 px)
            ├── 02_annotated.png       # con banner de resultado + defectos
            ├── 03_socket_crop.png     # crop del socket que vio el VLM
            ├── 04_cv_analysis.png     # medición CV: pines en verde, defectos en rojo
            └── metadata.json          # id, serial, AI result, modelo, ms
```

Protegido contra path traversal (whitelist `[A-Za-z0-9._-]` por componente +
resolución dentro de la base).

## Robustez de la IA

- **Retries** con backoff exponencial (`AI_MAX_RETRIES`).
- **Circuit breaker**: tras agotar reintentos se abre y rechaza peticiones
  inmediatas (503) hasta que una siguiente inferencia exitosa lo cierre.
- Broadcast `AI_SERVICE_DOWN` / `AI_SERVICE_RECOVERED` al fallar/recuperarse.
- **Degradación CV-only**: con la medición CV hecha y el VLM caído, la
  inspección NO se bloquea: se devuelve el resultado solo CV
  (`detection_method="CV"`).
- **Velocidad para producción**: el modo *thinking* de Qwen3 está desactivado
  por peticion (`enable_thinking=false`, con reintento automático si el
  servidor no soporta el campo), el prompt de verificacion exige JSON compacto
  y el timeout está acotado a 60 s. Medido en placa real: ~90 s antes →
  ~11-17 s por inspección.

## Decisiones / desviaciones del diseño original

1. **1 worker de uvicorn** (el diseño proponía 4): el broadcast WebSocket usa
   un singleton in-process (`ws_manager`); con >1 worker las estaciones solo
   recibirían los eventos de su worker. Para escalar a multi-worker se
   requiere un bus compartido (Redis pub/sub).
2. **Esquema sin autenticación** (B-002 pendiente): la tabla `users` existe de
   cara a futuro (JWT/roles). En red industrial segmentada se acepta sin auth.
3. El prompt del modelo vive en `app/config.py` (`DEFAULT_SYSTEM_PROMPT`) y es
   editable en `config.yaml` bajo `ai.system_prompt`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

Cobertura: procesamiento de imágenes (normalización/anotación), cliente IA
con transporte mock (parseo JSON, circuit breaker, listado de modelos),
almacenamiento de evidencia (estructura, colisiones, path traversal) y
WebSocket + flujo completo de inspección (broadcast, override, validaciones,
healthchecks, config) sobre SQLite (in-memory para API, archivo para
integración WS).

## Operación

```bash
bash scripts/healthcheck.sh   # estado de endpoints + contenedores
bash scripts/backup.sh        # pg_dump + evidencia (retiene 14)
```
