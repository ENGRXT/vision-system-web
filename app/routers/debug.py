"""Endpoint de debug: guarda diagnosticos del navegador (camara) a disco.

Solo para troubleshooting en campo. No expone datos sensibles: solo
reporta estado de permisos/camara del cliente.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel

router = APIRouter(tags=["debug"])

DIAG_DIR = Path("/app/logs")
DIAG_PATH = DIAG_DIR / "browser_diag.json"


class DiagResponse(BaseModel):
    ok: bool = True
    message: str = "diagnostico guardado"


@router.post("/api/debug/browser", response_model=DiagResponse)
async def save_browser_diag(request: Request) -> DiagResponse:
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        return DiagResponse(ok=False, message="json invalido")

    try:
        DIAG_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "client_ip": request.client.host if request.client else None,
            "data": payload,
        }
        DIAG_PATH.write_text(
            json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info(f"browser diag saved from {request.client.host}")
    except Exception as exc:  # pragma: no cover
        logger.warning(f"browser diag save failed: {exc}")

    return DiagResponse()
