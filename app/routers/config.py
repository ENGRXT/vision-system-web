"""Configuracion del sistema (admin) + descubrimiento de modelos IA."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import WSEvent
from app.schemas.config import (
    AIConnectionTestResponse,
    ConfigResponse,
    ConfigUpdateRequest,
)
from app.schemas.inspection import AIModelsResponse
from app.schemas.websocket import EVENT_CONFIG_UPDATED
from app.services.ai_proxy import ai_proxy_service
from app.services.websocket_manager import ws_manager
from app.utils.validators import SOCKET_TYPES

router = APIRouter(tags=["config"])

UPDATABLE_FIELDS = (
    "ai_base_url",
    "ai_model_name",
    "ai_timeout_seconds",
    "ai_max_retries",
    "log_level",
)


@router.get("/api/config", response_model=ConfigResponse)
async def get_config():
    masked = settings.database_url
    try:
        if "@" in masked and "://" in masked:
            prefix, rest = masked.split("://", 1)
            creds, host = rest.split("@", 1)
            user = creds.split(":")[0]
            masked = f"{prefix}://{user}:****@{host}"
    except Exception:
        pass
    return ConfigResponse(
        app_name=settings.app_name,
        app_version=settings.app_version,
        ai_base_url=settings.ai_base_url,
        ai_model_name=settings.ai_model_name,
        ai_timeout_seconds=settings.ai_timeout_seconds,
        ai_max_retries=settings.ai_max_retries,
        storage_base_path=settings.storage_base_path,
        log_level=settings.log_level,
        socket_types=list(SOCKET_TYPES),
        active_ws_connections=ws_manager.get_connection_count(),
        database_url=masked,
    )


@router.put("/api/config", response_model=ConfigResponse)
async def update_config(
    body: ConfigUpdateRequest, db: AsyncSession = Depends(get_db)
):
    changes: dict[str, str] = {}
    for field in UPDATABLE_FIELDS:
        new_value = getattr(body, field)
        if new_value is not None:
            old_value = getattr(settings, field)
            if str(old_value) != str(new_value):
                changes[field] = f"{old_value} -> {new_value}"
                setattr(settings, field, new_value)

    if changes:
        logger.info(f"Config updated: {changes} by {body.updated_by or 'unknown'}")
        event_payload = {
            "updated_by": body.updated_by or "unknown",
            "changes": changes,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await ws_manager.broadcast(EVENT_CONFIG_UPDATED, event_payload)
        try:
            row = (
                await db.execute(
                    select(WSEvent).where(WSEvent.event_type == EVENT_CONFIG_UPDATED)
                )
            ).scalar_one_or_none()
            if row is None:
                row = WSEvent(event_type=EVENT_CONFIG_UPDATED, payload={})
                db.add(row)
            row.payload = event_payload
            row.broadcasted_at = datetime.now(timezone.utc)
            await db.commit()
        except Exception as exc:
            logger.warning(f"No se pudo persistir CONFIG_UPDATED: {exc}")

    return await get_config()


@router.post("/api/config/test-ai-connection", response_model=AIConnectionTestResponse)
async def test_ai_connection():
    ok, detail, models = await ai_proxy_service.check_connection()
    return AIConnectionTestResponse(
        ok=ok,
        base_url=settings.ai_base_url,
        detail=detail,
        models=models,
    )


@router.get("/api/ai/models", response_model=AIModelsResponse)
async def list_ai_models():
    try:
        data = await ai_proxy_service.list_models()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"AI service error: {exc}")
    return AIModelsResponse(object="list", data=data)
