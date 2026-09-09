"""Router WebSocket: /ws/notifications (broadcast en tiempo real)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.schemas.websocket import (
    EVENT_STATION_CONNECTED,
    EVENT_STATION_DISCONNECTED,
)
from app.services.websocket_manager import ws_manager

router = APIRouter()

PONG_INTERVAL = 60.0


@router.websocket("/ws/notifications")
async def websocket_endpoint(
    websocket: WebSocket,
    station_id: str = Query(default="UNKNOWN"),
    operator_id: str = Query(default=""),
):
    await ws_manager.connect(websocket, station_id)
    await ws_manager.broadcast(
        EVENT_STATION_CONNECTED,
        {
            "station_id": station_id,
            "operator_id": operator_id or None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        global_event=True,
    )
    try:
        while True:
            data = await asyncio.wait_for(
                websocket.receive_text(),
                timeout=PONG_INTERVAL,
            )
            if data == "ping":
                await websocket.send_text("pong")
    except (WebSocketDisconnect, asyncio.TimeoutError):
        await ws_manager.broadcast(
            EVENT_STATION_DISCONNECTED,
            {
                "station_id": station_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            global_event=True,
        )
    finally:
        await ws_manager.disconnect(websocket)
