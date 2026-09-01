"""Tabla: inspections."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# JSON portable (SQLite en tests) / JSONB nativo en PostgreSQL (produccion)
JSONType = JSON().with_variant(postgresql.JSONB(), "postgresql")


class Inspection(Base):
    __tablename__ = "inspections"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    serial_number: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    date_folder: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    month_folder: Mapped[str] = mapped_column(String(20), nullable=False)
    station_id: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    operator_id: Mapped[Optional[str]] = mapped_column(String(50))
    socket_type_hint: Mapped[Optional[str]] = mapped_column(String(50))
    socket_type_detected: Mapped[Optional[str]] = mapped_column(String(50))
    ai_status: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    ai_defect_count: Mapped[int] = mapped_column(Integer, default=0)
    ai_defect_locations: Mapped[list] = mapped_column(
        JSONType, default=list, nullable=False
    )
    ai_confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(3, 2))
    ai_observations: Mapped[Optional[str]] = mapped_column(Text)
    ai_model_used: Mapped[Optional[str]] = mapped_column(String(100))
    ai_inference_time_ms: Mapped[Optional[int]] = mapped_column(Integer)
    ai_raw_response: Mapped[Optional[str]] = mapped_column(Text)
    final_disposition: Mapped[str] = mapped_column(String(20), nullable=False)
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_override_reason: Mapped[Optional[str]] = mapped_column(Text)
    evidence_folder_path: Mapped[str] = mapped_column(Text, nullable=False)
    original_image_name: Mapped[str] = mapped_column(
        String(100), default="01_original.png"
    )
    annotated_image_name: Mapped[str] = mapped_column(
        String(100), default="02_annotated.png"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
