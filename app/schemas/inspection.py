"""Schemas de inspeccion."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class DefectSchema(BaseModel):
    """Defecto: descripcion en espanol + caja delimitadora en la imagen."""

    description: str = ""
    # [x1, y1, x2, y2] como fracciones 0.0-1.0 del ancho/alto; None si no se localizo
    bbox: Optional[list[float]] = None


class InspectionResponse(BaseModel):
    inspection_id: uuid.UUID
    serial_number: str
    timestamp: datetime
    status: str
    defect_count: int
    defect_locations: list[str] = Field(default_factory=list)
    defects: list[DefectSchema] = Field(default_factory=list)
    pin_count: Optional[int] = None
    confidence: float
    observations: str
    socket_type_detected: str
    station_id: str
    # Region del socket analizada (fracciones de la imagen completa);
    # None si se analizo la imagen completa (sin localizacion)
    socket_bbox: Optional[list[float]] = None
    # URL del recorte del socket (lo que vio la IA); None si no hay crop
    socket_crop_url: Optional[str] = None
    # URL del render de la medicion CV (pines contados + defectos);
    # None si no hubo medicion CV
    cv_analysis_url: Optional[str] = None
    # Metodo de deteccion: HYBRID (CV+VLM), CV (solo medicion) o VLM
    # (camino clasico sin medicion CV)
    detection_method: Optional[str] = None
    evidence_path: str
    annotated_image_url: str
    inference_time_ms: int
    ai_model_used: str


class OverrideRequest(BaseModel):
    disposition: str = Field(..., pattern="^(PASS|FAIL)$")
    reason: Optional[str] = None
    operator_id: Optional[str] = None


class OverrideResponse(BaseModel):
    inspection_id: uuid.UUID
    serial_number: str
    ai_status: str
    final_disposition: str
    manual_override: bool
    manual_override_reason: Optional[str] = None


class InspectionSummary(BaseModel):
    inspection_id: uuid.UUID
    serial_number: str
    timestamp: datetime
    station_id: str
    operator_id: Optional[str] = None
    ai_status: str
    ai_defect_count: int
    ai_pin_count: Optional[int] = None
    ai_confidence: Optional[float] = None
    socket_type_detected: Optional[str] = None
    final_disposition: str
    manual_override: bool
    annotated_image_url: Optional[str] = None

    model_config = {"from_attributes": True}


class InspectionListResponse(BaseModel):
    total: int
    items: list[InspectionSummary]


class AIModelsResponse(BaseModel):
    object: str = "list"
    data: list[dict] = Field(default_factory=list)
