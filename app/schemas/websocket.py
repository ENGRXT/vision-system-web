"""Schemas de eventos WebSocket."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class WSEvent(BaseModel):
    """Formato de cada mensaje emitido por /ws/notifications."""

    event: str
    data: dict[str, Any]
    timestamp: datetime


class WSStationInfo(BaseModel):
    station_id: str
    operator_id: Optional[str] = None
    timestamp: datetime


EVENT_STATION_CONNECTED = "STATION_CONNECTED"
EVENT_STATION_DISCONNECTED = "STATION_DISCONNECTED"
EVENT_INSPECTION_STARTED = "INSPECTION_STARTED"
EVENT_INSPECTION_COMPLETED = "INSPECTION_COMPLETED"
EVENT_DEFECT_DETECTED = "DEFECT_DETECTED"
EVENT_AI_SERVICE_DOWN = "AI_SERVICE_DOWN"
EVENT_AI_SERVICE_RECOVERED = "AI_SERVICE_RECOVERED"
EVENT_CONFIG_UPDATED = "CONFIG_UPDATED"
