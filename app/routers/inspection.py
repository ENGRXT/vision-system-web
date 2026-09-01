"""Endpoint principal de inspeccion: POST /api/inspection."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Inspection
from app.schemas.inspection import (
    InspectionListResponse,
    InspectionResponse,
    InspectionSummary,
    OverrideRequest,
    OverrideResponse,
)
from app.schemas.websocket import (
    EVENT_DEFECT_DETECTED,
    EVENT_INSPECTION_COMPLETED,
    EVENT_INSPECTION_STARTED,
)
from app.services.ai_proxy import ai_proxy_service
from app.services.image_processor import encode_image_base64
from app.services.storage_service import storage_service
from app.services.websocket_manager import ws_manager
from app.utils.validators import (
    MAX_IMAGE_BYTES,
    is_allowed_image,
    is_valid_serial,
    is_valid_socket_type,
)

router = APIRouter(tags=["inspection"])


def _annotated_url(month: str, date: str, folder_name: str, filename: str) -> str:
    return f"/api/evidence/{month}/{date}/{folder_name}/{filename}"


@router.post("/api/inspection", response_model=InspectionResponse)
async def create_inspection(
    image: UploadFile = File(..., description="Imagen del socket (PNG/JPEG)"),
    serial_number: str = Form(...),
    socket_type: str = Form("AUTO"),
    station_id: str = Form(...),
    operator_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    serial = serial_number.strip().upper()
    if not is_valid_serial(serial):
        raise HTTPException(
            status_code=422,
            detail="serial_number invalido (4-50 caracteres alfanumericos)",
        )
    socket_type = socket_type.strip().upper() or "AUTO"
    if not is_valid_socket_type(socket_type):
        raise HTTPException(
            status_code=422,
            detail="socket_type debe ser INTEL_LGA1700, AMD_AM5 o AUTO",
        )
    if not is_allowed_image(image.content_type):
        raise HTTPException(
            status_code=415,
            detail="content_type no soportado (PNG/JPEG/WebP)",
        )

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="imagen vacia")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="imagen excede 20MB")

    inspection_id = uuid.uuid4()
    timestamp = datetime.now(timezone.utc)

    await ws_manager.broadcast(
        EVENT_INSPECTION_STARTED,
        {
            "inspection_id": str(inspection_id),
            "serial": serial,
            "station_id": station_id,
            "timestamp": timestamp.isoformat(),
        },
    )

    start = time.monotonic()
    image_b64 = encode_image_base64(image_bytes)
    try:
        ai_result = await ai_proxy_service.analyze_socket(
            image_b64, serial, socket_type
        )
    except Exception as exc:
        logger.error(f"AI failure for serial {serial}: {exc}")
        raise HTTPException(status_code=503, detail=f"AI service error: {exc}")
    inference_time_ms = int((time.monotonic() - start) * 1000)

    month_folder = timestamp.strftime("%B").upper()
    date_folder = timestamp.strftime("%m-%d-%Y")

    metadata = {
        "inspection_id": str(inspection_id),
        "serial_number": serial,
        "timestamp": timestamp.isoformat(),
        "station_id": station_id,
        "operator_id": operator_id,
        "socket_type_hint": socket_type,
        "ai_result": ai_result.model_dump(),
        "ai_model_used": ai_proxy_service.model_name,
        "ai_inference_time_ms": inference_time_ms,
    }
    folder = storage_service.save_inspection(
        serial, image_bytes, ai_result, metadata, timestamp=timestamp
    )

    record = Inspection(
        id=inspection_id,
        serial_number=serial,
        timestamp=timestamp,
        date_folder=date_folder,
        month_folder=month_folder,
        station_id=station_id,
        operator_id=operator_id,
        socket_type_hint=socket_type,
        socket_type_detected=ai_result.socket_type_detected,
        ai_status=ai_result.status,
        ai_defect_count=ai_result.defect_count,
        ai_defect_locations=ai_result.defect_locations,
        ai_confidence=Decimal(str(ai_result.confidence)),
        ai_observations=ai_result.observations,
        ai_model_used=ai_proxy_service.model_name,
        ai_inference_time_ms=inference_time_ms,
        final_disposition=(
            ai_result.status if ai_result.status in ("PASS", "FAIL") else "UNCERTAIN"
        ),
        evidence_folder_path=str(folder),
    )
    db.add(record)
    await db.commit()

    annotated_url = _annotated_url(
        month_folder, date_folder, folder.name, record.annotated_image_name
    )
    result_payload = {
        "inspection_id": str(inspection_id),
        "serial": serial,
        "status": ai_result.status,
        "confidence": ai_result.confidence,
        "station_id": station_id,
        "timestamp": timestamp.isoformat(),
    }
    await ws_manager.broadcast(EVENT_INSPECTION_COMPLETED, result_payload)

    if ai_result.status == "FAIL":
        await ws_manager.broadcast(
            EVENT_DEFECT_DETECTED,
            {
                **result_payload,
                "defect_count": ai_result.defect_count,
                "locations": ai_result.defect_locations,
                "image_url": annotated_url,
            },
        )

    return InspectionResponse(
        inspection_id=inspection_id,
        serial_number=serial,
        timestamp=timestamp,
        status=ai_result.status,
        defect_count=ai_result.defect_count,
        defect_locations=ai_result.defect_locations,
        confidence=ai_result.confidence,
        observations=ai_result.observations,
        socket_type_detected=ai_result.socket_type_detected,
        evidence_path=str(folder),
        annotated_image_url=annotated_url,
        inference_time_ms=inference_time_ms,
        ai_model_used=ai_proxy_service.model_name,
    )


@router.get("/api/inspections", response_model=InspectionListResponse)
async def list_inspections(
    serial_number: Optional[str] = Query(default=None),
    station_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(
        default=None, pattern="^(PASS|FAIL|UNCERTAIN)$"
    ),
    date_folder: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if serial_number:
        filters.append(Inspection.serial_number == serial_number.strip().upper())
    if station_id:
        filters.append(Inspection.station_id == station_id.strip())
    if status:
        filters.append(Inspection.ai_status == status)
    if date_folder:
        filters.append(Inspection.date_folder == date_folder)

    stmt = select(Inspection).order_by(Inspection.timestamp.desc())
    for f in filters:
        stmt = stmt.where(f)

    count_stmt = select(func.count(Inspection.id))
    for f in filters:
        count_stmt = count_stmt.where(f)
    total = (await db.execute(count_stmt)).scalar_one()

    rows = (await db.execute(stmt.limit(limit).offset(offset))).scalars().all()
    items = []
    for r in rows:
        folder_name = r.evidence_folder_path.rstrip("/").split("/")[-1]
        items.append(
            InspectionSummary(
                inspection_id=r.id,
                serial_number=r.serial_number,
                timestamp=r.timestamp,
                station_id=r.station_id,
                operator_id=r.operator_id,
                ai_status=r.ai_status,
                ai_defect_count=r.ai_defect_count or 0,
                ai_confidence=(
                    float(r.ai_confidence) if r.ai_confidence is not None else None
                ),
                socket_type_detected=r.socket_type_detected,
                final_disposition=r.final_disposition,
                manual_override=r.manual_override,
                annotated_image_url=(
                    _annotated_url(r.month_folder, r.date_folder, folder_name,
                                   r.annotated_image_name)
                    if r.evidence_folder_path
                    else None
                ),
            )
        )
    return InspectionListResponse(total=int(total), items=items)


@router.post(
    "/api/inspections/{inspection_id}/override",
    response_model=OverrideResponse,
)
async def override_disposition(
    inspection_id: uuid.UUID,
    body: OverrideRequest,
    db: AsyncSession = Depends(get_db),
):
    record = await db.get(Inspection, inspection_id)
    if record is None:
        raise HTTPException(status_code=404, detail="inspeccion no encontrada")

    record.final_disposition = body.disposition
    record.manual_override = True
    record.manual_override_reason = body.reason
    if body.operator_id:
        record.operator_id = body.operator_id
    await db.commit()
    await db.refresh(record)

    await ws_manager.broadcast(
        "INSPECTION_OVERRIDDEN",
        {
            "inspection_id": str(record.id),
            "serial": record.serial_number,
            "disposition": body.disposition,
            "reason": body.reason,
            "operator_id": body.operator_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
    return OverrideResponse(
        inspection_id=record.id,
        serial_number=record.serial_number,
        ai_status=record.ai_status,
        final_disposition=record.final_disposition,
        manual_override=record.manual_override,
        manual_override_reason=record.manual_override_reason,
    )
