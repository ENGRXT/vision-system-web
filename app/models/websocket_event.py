"""Tabla: websocket_events (audit log de eventos broadcast)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Uuid, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

JSONType = JSON().with_variant(postgresql.JSONB(), "postgresql")


class WSEvent(Base):
    __tablename__ = "websocket_events"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    event_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType)
    station_id: Mapped[str | None] = mapped_column(String(50), index=True)
    broadcasted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
