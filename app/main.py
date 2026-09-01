"""Punto de entrada FastAPI: app + lifespan + montaje de frontend."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from sqlalchemy import inspect

from app.config import settings
from app.database import Base, engine
from app import models  # noqa: F401  (registra tablas en Base.metadata)
from app.routers import config, evidence, health, inspection, websocket
from app.utils.logger import setup_logger
from app.utils.validators import SOCKET_TYPES  # noqa: F401

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


async def ensure_schema() -> None:
    """Crea tablas si no existen (dev) e idempotente con migraciones Alembic.

    En produccion la migracion oficial es `alembic upgrade head` (install.sh).
    Si las tablas no existen (despliegue sin migrar), se crean y se marca
    alembic en `head` para mantener consistencia.
    """
    async with engine.begin() as conn:
        has_tables = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).has_table("inspections")
        )
    if has_tables:
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.warning(
        "Schema no migado: se ejecuto create_all (dev). "
        "En produccion usar: alembic upgrade head"
    )
    try:
        from alembic import command
        from alembic.config import Config

        alembic_ini = BASE_DIR.parent / "alembic.ini"
        if alembic_ini.exists():
            cfg = Config(str(alembic_ini))
            command.stamp(cfg, "head")
            logger.info("Alembic marcado en head (stamp)")
    except Exception as exc:
        logger.warning(f"No se pudo stamp alembic: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger(log_file="logs/app.log" if settings.log_level != "DEBUG" else None)
    logger.info(f"Iniciando {settings.app_name} v{settings.app_version}")
    Path(settings.storage_base_path).mkdir(parents=True, exist_ok=True)
    try:
        await ensure_schema()
    except Exception as exc:
        logger.error(f"No se pudo verificar schema: {exc}")
    logger.info("VISION-SYSTEM WEB listo")
    yield
    logger.info("Deteniendo VISION-SYSTEM WEB")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Sistema Web de Inspeccion Automatizada de Pines en Sockets de "
        "Motherboards - Deteccion de pines doblados mediante VLM local "
        "(Qwen3-VL-32B-Instruct-FP8 via vLLM)."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(inspection.router)
app.include_router(evidence.router)
app.include_router(config.router)
app.include_router(websocket.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC_DIR / "assets" / "favicon.ico", media_type="image/x-icon")
