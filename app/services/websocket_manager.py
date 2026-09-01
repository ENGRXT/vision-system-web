"""Gestor de conexiones WebSocket + broadcast (singleton)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import List

from fastapi import WebSocket
from loguru import logger


class WebSocketManager:
    """Lista de conexiones compartida + lock por loop de eventos.

    El lock es por loop para evitar 'bound to a different event loop'
    cuando conviven varios loops (tests, worker, TestClient portal).
    """

    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []
        self._locks: dict = {}

    def _lock_for_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[loop] = lock
        return lock

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock_for_loop():
            self.active_connections.append(websocket)
        logger.info(
            f"WS client connected. Total: {len(self.active_connections)}"
        )

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock_for_loop():
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info(
            f"WS client disconnected. Total: {len(self.active_connections)}"
        )

    async def broadcast(self, event_type: str, payload: dict) -> None:
        message = json.dumps(
            {
                "event": event_type,
                "data": payload,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            default=str,
        )
        disconnected: list[WebSocket] = []
        async with self._lock_for_loop():
            connections = self.active_connections.copy()

        for connection in connections:
            try:
                await connection.send_text(message)
            except Exception as exc:
                logger.warning(f"WS send failed: {exc}")
                disconnected.append(connection)

        for conn in disconnected:
            await self.disconnect(conn)

    def get_connection_count(self) -> int:
        return len(self.active_connections)


# Singleton global
ws_manager = WebSocketManager()
