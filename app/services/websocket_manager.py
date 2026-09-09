"""Gestor de conexiones WebSocket + broadcast dirigido por estacion (singleton)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import WebSocket
from loguru import logger

# Conexiones que actuan como "muro de monitoreo": reciben todos los eventos
# de inspeccion (dashboards futuros / admin). Solo marcas EXPLICITAS.
# Default seguro: una estacion desconocida/vacia (UNKNOWN) NUNCA recibe
# eventos de inspeccion de otra estacion, asi un cliente con identificacion
# erronea no provoca fuga de resultados entre equipos.
WALLBOARD_STATIONS = {"ALL", "ADMIN"}


class WebSocketManager:
    """Lista de conexiones (websocket + estacion) + lock por loop de eventos.

    El lock es por loop para evitar 'bound to a different event loop'
    cuando conviven varios loops (tests, worker, TestClient portal).

    Politica de entrega:
    - broadcast(event, payload, station_id=X): solo a la estacion X
      (mas las conexiones wallboard). Evita que una estacion vea los
      resultados de otra cuando hay varios equipos en paralelo.
    - broadcast(event, payload, global_event=True): a todos (estados del
      sistema: IA caido/recuperado, estaciones conectadas, config).
    - broadcast(event, payload) sin destino: a todos (compatibilidad).
    """

    def __init__(self) -> None:
        # [(websocket, station_id), ...]
        self.active_connections: List[tuple[WebSocket, str]] = []
        self._locks: dict = {}

    def _lock_for_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[loop] = lock
        return lock

    async def connect(
        self, websocket: WebSocket, station_id: str = "UNKNOWN"
    ) -> None:
        await websocket.accept()
        async with self._lock_for_loop():
            self.active_connections.append((websocket, station_id))
        logger.info(
            f"WS client connected ({station_id}). Total: {len(self.active_connections)}"
        )

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock_for_loop():
            self.active_connections = [
                (ws, station)
                for ws, station in self.active_connections
                if ws is not websocket
            ]
        logger.info(
            f"WS client disconnected. Total: {len(self.active_connections)}"
        )

    async def broadcast(
        self,
        event_type: str,
        payload: dict,
        station_id: Optional[str] = None,
        global_event: bool = False,
    ) -> None:
        message = json.dumps(
            {
                "event": event_type,
                "data": payload,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            default=str,
        )

        async with self._lock_for_loop():
            connections = self.active_connections.copy()

        if not global_event and station_id:
            target = station_id.strip().upper()
            connections = [
                (ws, station)
                for ws, station in connections
                if station.strip().upper() == target
                or station.strip().upper() in WALLBOARD_STATIONS
            ]

        disconnected: list[WebSocket] = []
        for ws, _station in connections:
            try:
                await ws.send_text(message)
            except Exception as exc:
                logger.warning(f"WS send failed: {exc}")
                disconnected.append(ws)

        for conn in disconnected:
            await self.disconnect(conn)

    def get_connection_count(self) -> int:
        return len(self.active_connections)


# Singleton global
ws_manager = WebSocketManager()
