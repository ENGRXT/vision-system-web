"""Health checks industriales."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter
from sqlalchemy import text

from app.config import settings
from app.database import async_session_factory

router = APIRouter(tags=["health"])

AI_PROBE_TIMEOUT = 5.0


@router.get("/health")
async def health():
    return {
        "status": "healthy",
        "app": settings.app_name,
        "version": settings.app_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/live")
async def health_live():
    return {"status": "alive"}


@router.get("/health/ready")
async def health_ready():
    db_status = "ok"
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    ai_status = "ok"
    try:
        async with httpx.AsyncClient(timeout=AI_PROBE_TIMEOUT) as client:
            resp = await client.get(f"{settings.ai_base_url.rstrip('/')}/models")
            resp.raise_for_status()
    except Exception:
        ai_status = "error"

    ready = db_status == "ok" and ai_status == "ok"
    return {
        "status": "ready" if ready else "degraded",
        "checks": {"database": db_status, "ai_service": ai_status},
    }
