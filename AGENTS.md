# AGENTS.md — Reglas del proyecto

## Idioma
- **Todos los comentarios, docstrings, mensajes de error y textos de UI deben escribirse en ESPAÑOL.**
- Nombres de variables, funciones y archivos: en inglés (convención de código).
- Mensajes al usuario en español.

## Comandos
- Tests: `.venv/bin/python -m pytest -q` (raíz del repo; deben pasar 84 tests).
- Sin linter configurado en este repo.
- JS: `node --check app/static/js/*.js`.

## Arquitectura

### Pipeline híbrido CV + VLM (`app/services/ai_proxy.py` + `app/utils/socket_cv.py`)
El CV determinista MIDE (autoritativo); el VLM solo VERIFICA (1 llamada).
1. **Etapa 1 – Localizar (CV)**: `socket_cv.find_socket` (umbral adaptativo +
   morfología + componentes) localiza el campo de pines. Si no lo hace,
   fallback al VLM (`LOCATE_SOCKET_PROMPT`; JSON inválido o caja > 0.95 =
   "sin socket" → imagen completa, NUNCA bloquea).
2. **Etapa 2 – Crop + zoom** (`app/utils/image_ops.py`, Pillow puro): margen
   12%, clamp, región <8px → full; lado mayor <1024 → ×2 LANCZOS, >2048 →
   escala a 2048.
3. **Etapa 3 – Medir (CV)**: `socket_cv.analyze_pins` sobre la imagen
   COMPLETA (restringida a la región localizada CV/VLM; si no hay región, la
   autodetecta por densidad). Las bboxes de defecto salen en fracciones de la
   imagen completa. Con conteo EXACTO por retícula (peaks por columna/fila),
   tipo por conteo de referencia (`SOCKET_PIN_REFS`, tolerancias 10/30/60),
   pines faltantes (celdas vacías con vecinos) y doblados (componentes fuera
   de retícula). Si el CV dijo UNKNOWN y el operador confirmó tipo, se usa el
   confirmado.
4. **Etapa 4 – Verificar (VLM)**: `USER_PROMPT_VERIFY_TEMPLATE` (1 llamada
   sobre el crop ampliado, con conteo/tipo medidos en el prompt; los defectos
   CV se listan en coords del CROP con `full_to_crop_frac`, porque el VLM solo
   ve el crop). Sin medición CV: camino clásico 2 llamadas (localizar +
   analizar) con `USER_PROMPT_TEMPLATE`/CONFIRMED. Las bboxes de defecto del
   VLM (coords del crop) se remapean con `remap_bbox_frac` a la imagen
   completa ANTES de la fusión; las del CV ya salen en imagen completa.
5. **Fusión** (`_merge_cv_vlm`): conteo/tipo SIEMPRE del CV; defectos = CV +
   VLM (dedup por solapamiento de bbox); FAIL si hay algún defecto.
- `detection_method`: `HYBRID` (CV+VLM), `CV` (IA caída pero el CV midió →
  NO bloquea), `VLM` (sin medición CV). Circuit breaker abierto: con
  medición CV → solo-CV sin llamar al VLM; sin ella → 503. Se captura el
  estado ANTES de las llamadas de la inspeccion (`circuit_was_open`) para que
  la llamada que abre el circuito propague su error original.
- `normalize_image` (PNG, RGB, max 1600 px) es el ÚNICO marco de referencia.
- `AIResult.socket_bbox` (fracciones) + `crop_base64` + `cv_analysis_base64`
  (`exclude=True`, no entran a metadata.json). Evidencia:
  `03_socket_crop.png` + caja verde "SOCKET" en `02_annotated.png` +
  `04_cv_analysis.png` (pines medidos en verde, defectos en rojo).
- UI: la card de resultado muestra el método de detección
  (`result-detection-method`) y botón de evidencia CV (`btn-cv-analysis`).

### Modelo de aislamiento multi-estación (paralelo sin mezclar)
- **S1** Carpeta de evidencia atómica única por inspección
  (`storage_service.get_inspection_folder`, mkdir sin `exist_ok`).
- **S2** Eventos WS dirigidos por estación; estado global (AI down/recovered)
  para todas; estaciones desconectas reciben `INSPECTION_UNKNOWN`.
- **S3** HTTP directo: la respuesta incluye `inspection_id` + `station_id`;
  cada URL de evidencia es única por carpeta.
- **S4** Lock por estación (`_inflight` en `routers/inspection.py`): 2.º POST
  simultáneo de la MISMA estación → 429 fail-fast (antes del WS STARTED);
  estaciones distintas corren en paralelo sin límite; se libera en `finally`
  (incluso 503/crash).
- **S5** Trazabilidad UI: card de resultado muestra Serial + ID de inspección
  (primeros 8) + botón "Ver zoom del socket" (`socket_crop_url`); el JS solo
  renderiza resultados de su estación (`_isLocalStation`) y usa `_inspectionSeq`
  contra respuestas tardías.
- **S6** Frontend sin estado global de inspección: cada estación opera su
  propia fila de resultado (badge "OTRA ESTACIÓN" si no coincide).

## Entorno
- Backend: FastAPI + SQLAlchemy (async), PostgreSQL (prod) / SQLite (dev).
- IA: vLLM Qwen3-VL (`http://10.145.123.27:8000/v1`, modelo `qwen3-vl`).
  - IMPORTANTE: esta build devuelve la respuesta en `message["reasoning"]` (thinking mode)
    con `content: null` → `ai_proxy.py` debe caer a `reasoning`/`reasoning_content`.
- Frontend: HTML/JS estático servido por la API (`/static`), nginx como proxy.
- Docker local: `docker/docker-compose.local.yml` (puertos 8090 HTTP / 9443 HTTPS / 15432 PG).
  Puertos prod: 8080 / 443.
