"""Endpoint de evidencia: listado y descarga de archivos."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.services.storage_service import storage_service

router = APIRouter(prefix="/api/evidence", tags=["evidence"])

CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".json": "application/json",
}


@router.get("/list")
async def list_evidence(
    month: Optional[str] = Query(default=None),
    date: Optional[str] = Query(default=None),
):
    try:
        return storage_service.list_evidence(month, date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/{month}/{date}/{folder}/{filename}")
async def get_evidence_file(
    month: str,
    date: str,
    folder: str,
    filename: str,
):
    path = storage_service.resolve_file(month, date, folder, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="archivo no encontrado")
    media_type = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type)
