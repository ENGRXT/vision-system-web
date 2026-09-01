"""Fixtures de test: SQLite en memoria (API) + SQLite en archivo (WS integracion).

IMPORTANTE: las variables de entorno se fijan ANTES de importar app.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

_SESSION_TMP = Path(tempfile.mkdtemp(prefix="vs_test_"))

os.environ.setdefault("STORAGE_BASE_PATH", str(_SESSION_TMP / "evidence"))
os.environ.setdefault(
    "DATABASE_URL", f"sqlite+aiosqlite:///{_SESSION_TMP / 'integration.db'}"
)
os.environ.setdefault("AI_MAX_RETRIES", "0")
os.environ.setdefault("AI_TIMEOUT_SECONDS", "5")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("VISION_CONFIG", "")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  (registra tablas)
from app.database import Base, engine as app_engine, get_db
from app.main import app as fastapi_app
from app.services.websocket_manager import ws_manager


@pytest.fixture(autouse=True)
def _reset_ws_connections():
    ws_manager.active_connections.clear()
    yield
    ws_manager.active_connections.clear()


# --------------------------------------------------------------------- #
# API tests (async, engine in-memory + override de get_db)
# --------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(db_factory):
    async with db_factory() as session:
        yield session


@pytest_asyncio.fixture
async def app(db_factory):
    async def override_get_db():
        async with db_factory() as session:
            yield session

    fastapi_app.dependency_overrides[get_db] = override_get_db
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        yield c


# --------------------------------------------------------------------- #
# Integracion WS (TestClient sincrono, engine real de app, SQLite archivo)
# --------------------------------------------------------------------- #


@pytest.fixture
def integration_client():
    from starlette.testclient import TestClient

    with TestClient(fastapi_app) as tc:
        yield tc
    # Evita que conexiones aiosqlite envejecidas (loop cerrado) se reutilicen
    asyncio.run(app_engine.dispose())


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    from app.services.storage_service import storage_service

    target = tmp_path / "evidence"
    target.mkdir()
    monkeypatch.setattr(storage_service, "base_path", target)
    return target


@pytest.fixture
def mock_ai_pass(monkeypatch):
    from app.services.ai_proxy import AIResult, ai_proxy_service

    async def fake_analyze(image_base64, serial, socket_hint=""):
        return AIResult(
            status="PASS",
            defect_count=0,
            defect_locations=[],
            confidence=0.98,
            observations="Sin defectos",
            socket_type_detected="INTEL_LGA1700",
        )

    monkeypatch.setattr(ai_proxy_service, "analyze_socket", fake_analyze)
    return fake_analyze


@pytest.fixture
def mock_ai_fail(monkeypatch):
    from app.services.ai_proxy import AIResult, ai_proxy_service

    async def fake_analyze(image_base64, serial, socket_hint=""):
        return AIResult(
            status="FAIL",
            defect_count=2,
            defect_locations=["Pin 12: doblado", "Zona B4: falta pin"],
            confidence=0.91,
            observations="Pines doblados detectados",
            socket_type_detected="AMD_AM5",
        )

    monkeypatch.setattr(ai_proxy_service, "analyze_socket", fake_analyze)
    return fake_analyze


@pytest.fixture
def mock_ai_down(monkeypatch):
    from app.services.ai_proxy import ai_proxy_service

    async def fake_analyze(image_base64, serial, socket_hint=""):
        raise RuntimeError("AI service unreachable")

    monkeypatch.setattr(ai_proxy_service, "analyze_socket", fake_analyze)
    return fake_analyze
