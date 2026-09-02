# AGENTS.md — Reglas del proyecto

## Idioma
- **Todos los comentarios, docstrings, mensajes de error y textos de UI deben escribirse en ESPAÑOL.**
- Nombres de variables, funciones y archivos: en inglés (convención de código).
- Mensajes al usuario en español.

## Comandos
- Tests: `.venv/bin/python -m pytest -q` (raíz del repo; deben pasar 33 tests).
- Sin linter configurado en este repo.

## Entorno
- Backend: FastAPI + SQLAlchemy (async), PostgreSQL (prod) / SQLite (dev).
- IA: vLLM Qwen3-VL (`http://10.145.123.27:8000/v1`, modelo `qwen3-vl`).
  - IMPORTANTE: esta build devuelve la respuesta en `message["reasoning"]` (thinking mode)
    con `content: null` → `ai_proxy.py` debe caer a `reasoning`/`reasoning_content`.
- Frontend: HTML/JS estático servido por la API (`/static`), nginx como proxy.
- Docker local: `docker/docker-compose.local.yml` (puertos 8090 HTTP / 9443 HTTPS / 15432 PG).
  Puertos prod: 8080 / 443.
