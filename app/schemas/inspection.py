"""Schemas de inspeccion."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class InspectionResponse(BaseModel):
    inspection_id: uuid.UUID
    serial_number: str
    timestamp: datetime
    status: str
    defect_count: int
    defect_locations: list[str] = Field(default_factory=list)
    confidence: float
    observations: str
    socket_type_detected: str
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
