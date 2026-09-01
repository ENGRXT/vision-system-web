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
