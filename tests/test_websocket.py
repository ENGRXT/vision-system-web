"""Tests de WebSocket + flujo completo de inspeccion (integracion, SQLite archivo)."""
from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

WS_URL = "/ws/notifications?station_id=QA-01&operator_id=OP001"


def _png_bytes() -> bytes:
    img = Image.new("RGB", (640, 480), (110, 110, 110))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _post_inspection(tc, serial: str = "WSTEST01", socket_type: str = "AUTO",
                     content_type: str = "image/png"):
    return tc.post(
        "/api/inspection",
        files={"image": ("socket.png", _png_bytes(), content_type)},
        data={
            "serial_number": serial,
            "socket_type": socket_type,
            "station_id": "QA-01",
            "operator_id": "OP001",
        },
    )


def test_ws_connect_receives_station_connected(integration_client):
    tc = integration_client
    with tc.websocket_connect(WS_URL) as ws:
        msg = ws.receive_json()
        assert msg["event"] == "STATION_CONNECTED"
        assert msg["data"]["station_id"] == "QA-01"
        assert msg["data"]["operator_id"] == "OP001"
        assert "timestamp" in msg


def test_ws_ping_pong(integration_client):
    tc = integration_client
    with tc.websocket_connect(WS_URL) as ws:
        ws.receive_json()  # STATION_CONNECTED
        ws.send_text("ping")
        assert ws.receive_text() == "pong"


def test_ws_disconnect_broadcasts_event(integration_client):
    tc = integration_client
    with tc.websocket_connect(WS_URL) as ws_a:
        ws_a.receive_json()  # su propio STATION_CONNECTED
        with tc.websocket_connect(
            "/ws/notifications?station_id=QA-02"
        ) as ws_b:
            connected = ws_a.receive_json()
            assert connected["event"] == "STATION_CONNECTED"
            assert connected["data"]["station_id"] == "QA-02"
            ws_b.receive_json()  # su propio STATION_CONNECTED
        disconnected = ws_a.receive_json()
        assert disconnected["event"] == "STATION_DISCONNECTED"
        assert disconnected["data"]["station_id"] == "QA-02"


def test_full_inspection_flow_broadcasts(integration_client, storage_dir, mock_ai_fail):
    tc = integration_client
    with tc.websocket_connect(WS_URL) as ws:
        ws.receive_json()  # STATION_CONNECTED

        resp = _post_inspection(tc, serial="WSTEST01")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "FAIL"
        assert data["defect_count"] == 2
        assert data["annotated_image_url"].startswith("/api/evidence/")

        started = ws.receive_json()
        assert started["event"] == "INSPECTION_STARTED"
        assert started["data"]["serial"] == "WSTEST01"

        completed = ws.receive_json()
        assert completed["event"] == "INSPECTION_COMPLETED"
        assert completed["data"]["status"] == "FAIL"

        defect = ws.receive_json()
        assert defect["event"] == "DEFECT_DETECTED"
        assert defect["data"]["defect_count"] == 2
        assert defect["data"]["image_url"] == data["annotated_image_url"]

        # La imagen anotada es servible via /api/evidence
        img_resp = tc.get(data["annotated_image_url"])
        assert img_resp.status_code == 200
        assert img_resp.headers["content-type"] == "image/png"


def test_inspection_listed_and_override(integration_client, storage_dir, mock_ai_pass):
    tc = integration_client
    resp = _post_inspection(tc, serial="OVR0001")
    assert resp.status_code == 200, resp.text
    inspection_id = resp.json()["inspection_id"]

    listing = tc.get("/api/inspections", params={"serial_number": "OVR0001"})
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] >= 1
    assert body["items"][0]["serial_number"] == "OVR0001"
    assert body["items"][0]["ai_status"] == "PASS"

    ovr = tc.post(
        f"/api/inspections/{inspection_id}/override",
        json={
            "disposition": "FAIL",
            "reason": "Pin doblado no detectado por IA",
            "operator_id": "OP009",
        },
    )
    assert ovr.status_code == 200, ovr.text
    ovr_body = ovr.json()
    assert ovr_body["final_disposition"] == "FAIL"
    assert ovr_body["manual_override"] is True

    listing2 = tc.get("/api/inspections", params={"serial_number": "OVR0001"})
    item = next(i for i in listing2.json()["items"]
                if i["inspection_id"] == inspection_id)
    assert item["final_disposition"] == "FAIL"
    assert item["manual_override"] is True


def test_inspection_validation_errors(integration_client, storage_dir, mock_ai_pass):
    tc = integration_client
    r = _post_inspection(tc, serial="ab")  # serial demasiado corto
    assert r.status_code == 422

    r = _post_inspection(tc, serial="VAL001", socket_type="INVALID_TYPE")
    assert r.status_code == 422

    r = _post_inspection(tc, serial="VAL002", content_type="text/plain")
    assert r.status_code == 415


def test_inspection_ai_down_returns_503(integration_client, storage_dir, mock_ai_down):
    tc = integration_client
    r = _post_inspection(tc, serial="DOWN01")
    assert r.status_code == 503
    assert "AI service error" in r.json()["detail"]


def test_health_endpoints(integration_client):
    tc = integration_client
    r = tc.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"

    r = tc.get("/health/live")
    assert r.status_code == 200

    r = tc.get("/health/ready")
    assert r.status_code == 200
    assert set(r.json()["checks"]) == {"database", "ai_service"}
    assert r.json()["checks"]["database"] == "ok"


def test_config_get_and_put(integration_client, monkeypatch):
    from app.config import settings

    tc = integration_client
    r = tc.get("/api/config")
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["app_version"] == settings.app_version
    assert "****" in cfg["database_url"] or "@" not in cfg["database_url"].split("://")[-1]

    original = settings.ai_max_retries
    r = tc.put(
        "/api/config",
        json={"ai_max_retries": 3, "updated_by": "QA-01"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ai_max_retries"] == 3
    assert settings.ai_max_retries == 3

    # Persistencia de audit (tabla websocket_events)
    r = tc.get("/api/config")
    assert r.status_code == 200
    monkeypatch.setattr(settings, "ai_max_retries", original)


def test_index_serves_frontend(integration_client):
    tc = integration_client
    r = tc.get("/")
    assert r.status_code == 200
    assert "VISION-SYSTEM" in r.text

    r = tc.get("/static/js/app.js")
    assert r.status_code == 200


def test_inspection_events_only_reach_own_station(integration_client, storage_dir, mock_ai_fail):
    """Con 2 estaciones en paralelo, los eventos de inspeccion de la
    estacion A NO deben llegar al navegador de la estacion B (causa raiz
    de la confusion de imagenes/resultados entre equipos)."""
    import threading
    import time

    tc = integration_client
    received_b: list = []

    with tc.websocket_connect(WS_URL) as ws_a:  # estacion QA-01
        ws_a.receive_json()  # su propio STATION_CONNECTED

        with tc.websocket_connect("/ws/notifications?station_id=QA-02") as ws_b:
            ws_a.receive_json()  # STATION_CONNECTED de QA-02 (evento global)
            ws_b.receive_json()  # su propio STATION_CONNECTED

            # Drena QA-02 en segundo plano: debe recibir NADA de la inspeccion
            def _drain_b():
                try:
                    while True:
                        received_b.append(ws_b.receive_json())
                except Exception:
                    pass  # conexion cerrada

            t = threading.Thread(target=_drain_b, daemon=True)
            t.start()
            time.sleep(0.3)  # deja al hilo bloqueado en receive

            resp = _post_inspection(tc, serial="WSTEST02")  # desde QA-01
            assert resp.status_code == 200, resp.text

            # La estacion propietaria (QA-01) recibe sus 3 eventos
            started = ws_a.receive_json()
            assert started["event"] == "INSPECTION_STARTED"
            completed = ws_a.receive_json()
            assert completed["event"] == "INSPECTION_COMPLETED"
            defect = ws_a.receive_json()
            assert defect["event"] == "DEFECT_DETECTED"
            assert defect["data"]["station_id"] == "QA-01"

            time.sleep(0.5)  # margen para que (si hubiera fuga) llegara
            t.join(timeout=2)

    leak = [
        m for m in received_b
        if m["event"] in {"INSPECTION_STARTED", "INSPECTION_COMPLETED", "DEFECT_DETECTED"}
    ]
    assert not leak, f"QA-02 recibio eventos de inspeccion de QA-01: {leak}"


def test_unknown_station_receives_no_inspection_events(integration_client, storage_dir, mock_ai_fail):
    """Una conexion con estacion desconocida/vacia (UNKNOWN) NO recibe
    eventos de inspeccion de otra estacion (default seguro): si un cliente
    se conecta con identificacion erronea, no debe ver ni sonar con los
    resultados ajenos. Solo recibe eventos GLOBALES."""
    import threading
    import time

    tc = integration_client
    received_unknown: list = []

    with tc.websocket_connect(WS_URL) as ws_qa:  # estacion propietaria
        ws_qa.receive_json()  # su propio STATION_CONNECTED

        with tc.websocket_connect(
            "/ws/notifications?station_id=UNKNOWN"
        ) as ws_unknown:
            ws_qa.receive_json()  # STATION_CONNECTED de UNKNOWN (global)
            ws_unknown.receive_json()  # su propio STATION_CONNECTED

            def _drain_unknown():
                try:
                    while True:
                        received_unknown.append(ws_unknown.receive_json())
                except Exception:
                    pass  # conexion cerrada

            t = threading.Thread(target=_drain_unknown, daemon=True)
            t.start()
            time.sleep(0.3)

            resp = _post_inspection(tc, serial="UNKNOWNLEAK01")  # desde QA-01
            assert resp.status_code == 200, resp.text

            # La estacion propietaria recibe sus 3 eventos
            assert ws_qa.receive_json()["event"] == "INSPECTION_STARTED"
            assert ws_qa.receive_json()["event"] == "INSPECTION_COMPLETED"
            assert ws_qa.receive_json()["event"] == "DEFECT_DETECTED"

            time.sleep(0.5)
            t.join(timeout=2)

    leak = [
        m for m in received_unknown
        if m["event"] in {"INSPECTION_STARTED", "INSPECTION_COMPLETED", "DEFECT_DETECTED"}
    ]
    assert not leak, f"UNKNOWN recivio eventos de inspeccion ajenos: {leak}"


class _FakeWs:
    """Stub de WebSocket para probar la politica de entrega del manager."""

    def __init__(self, name: str):
        self.name = name
        self.sent: list[str] = []

    async def accept(self) -> None:
        return None

    async def send_text(self, message: str) -> None:
        self.sent.append(message)


async def test_broadcast_targeted_by_station():
    from app.schemas.websocket import EVENT_AI_SERVICE_DOWN, EVENT_DEFECT_DETECTED
    from app.services.websocket_manager import WebSocketManager

    mgr = WebSocketManager()
    ws_a = _FakeWs("A")
    ws_b = _FakeWs("B")
    ws_board = _FakeWs("board")
    await mgr.connect(ws_a, "QA-01")
    await mgr.connect(ws_b, "QA-02")
    await mgr.connect(ws_board, "ALL")  # muro de monitoreo

    # Evento de inspeccion dirigido: solo QA-01 + wallboard
    await mgr.broadcast(EVENT_DEFECT_DETECTED, {"station_id": "QA-01", "serial": "X1"},
                        station_id="QA-01")
    assert len(ws_a.sent) == 1
    assert ws_b.sent == []
    assert len(ws_board.sent) == 1

    # Estacion desconocida (identificacion erronea): solo eventos globales
    ws_unknown = _FakeWs("unknown")
    await mgr.connect(ws_unknown, "UNKNOWN")

    # Evento de inspeccion dirigido: NO llega a la estacion desconocida
    await mgr.broadcast(EVENT_DEFECT_DETECTED, {"station_id": "QA-01", "serial": "X2"},
                        station_id="QA-01")
    assert len(ws_a.sent) == 2
    assert ws_b.sent == []
    assert len(ws_board.sent) == 2
    assert ws_unknown.sent == []

    # Evento global: llega a todos, incluida la estacion desconocida
    await mgr.broadcast(EVENT_AI_SERVICE_DOWN, {"error_message": "x"}, global_event=True)
    assert len(ws_a.sent) == 3
    assert len(ws_b.sent) == 1
    assert len(ws_board.sent) == 3
    assert len(ws_unknown.sent) == 1
