"""Tests de aislamiento de inspecciones simultaneas.

Aislamiento S4: UNA sola inspeccion en vuelo por estacion (429 fail-fast);
estaciones distintas corren en paralelo. El lock se libera siempre,
incluso cuando la IA falla.
"""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from tests.conftest import png_bytes

POST = "/api/inspection"


def _files():
    return {"image": ("socket.png", png_bytes(), "image/png")}


def _data(serial: str, station: str) -> dict:
    return {
        "serial_number": serial,
        "socket_type": "AUTO",
        "station_id": station,
    }


async def _slow_analyze(image_bytes, serial, socket_hint=""):
    from app.services.ai_proxy import AIResult

    # Duraciones escalonadas: ambas inspecciones están EN VUELO a la vez,
    # pero los commits no se pisan (el engine de test SQLite comparte
    # conexion unica entre sesiones)
    delay = 0.3 if serial.endswith("1") else 0.5
    await asyncio.sleep(delay)
    return AIResult(
        status="PASS",
        defect_count=0,
        confidence=0.98,
        observations="ok",
        socket_type_detected="INTEL_LGA1151",
    )


async def test_same_station_second_post_returns_429(client, storage_dir, monkeypatch):
    from app.services.ai_proxy import ai_proxy_service

    monkeypatch.setattr(ai_proxy_service, "analyze_socket", _slow_analyze)

    t1 = asyncio.create_task(
        client.post(POST, files=_files(), data=_data("SERA1", "LOCK-01"))
    )
    # Espera a que la primera inspeccion tome el lock de la estacion
    await asyncio.sleep(0.15)

    r2 = await client.post(POST, files=_files(), data=_data("SERA2", "LOCK-01"))
    assert r2.status_code == 429
    assert "estacion" in r2.json()["detail"].lower()

    r1 = await t1
    assert r1.status_code == 200

    # Al terminar, la estacion queda libre para la siguiente
    r3 = await client.post(POST, files=_files(), data=_data("SERA3", "LOCK-01"))
    assert r3.status_code == 200


async def test_different_stations_run_in_parallel(client, storage_dir, monkeypatch):
    from app.services.ai_proxy import ai_proxy_service

    monkeypatch.setattr(ai_proxy_service, "analyze_socket", _slow_analyze)

    t1 = asyncio.create_task(
        client.post(POST, files=_files(), data=_data("SERB1", "PAR-01"))
    )
    t2 = asyncio.create_task(
        client.post(POST, files=_files(), data=_data("SERB2", "PAR-02"))
    )
    r1, r2 = await asyncio.gather(t1, t2)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["station_id"] == "PAR-01"
    assert r2.json()["station_id"] == "PAR-02"
    # Dos carpetas de evidencia distintas (cada inspeccion suya)
    assert r1.json()["evidence_path"] != r2.json()["evidence_path"]


async def test_lock_released_when_ai_fails(client, storage_dir, monkeypatch):
    from app.services.ai_proxy import AIResult, ai_proxy_service

    async def down(image_bytes, serial, socket_hint=""):
        await asyncio.sleep(0.05)
        raise RuntimeError("AI service unreachable")

    async def ok(image_bytes, serial, socket_hint=""):
        return AIResult(
            status="PASS",
            defect_count=0,
            confidence=0.98,
            observations="ok",
            socket_type_detected="INTEL_LGA1151",
        )

    monkeypatch.setattr(ai_proxy_service, "analyze_socket", down)
    r1 = await client.post(POST, files=_files(), data=_data("SERC1", "LOCK-02"))
    assert r1.status_code == 503

    # La estacion queda libre aunque la IA haya fallado
    monkeypatch.setattr(ai_proxy_service, "analyze_socket", ok)
    r2 = await client.post(POST, files=_files(), data=_data("SERC2", "LOCK-02"))
    assert r2.status_code == 200


async def test_response_exposes_socket_crop_url(client, storage_dir, monkeypatch):
    from app.services.ai_proxy import AIResult, ai_proxy_service

    crop = png_bytes(200, 150, (200, 50, 50))

    async def fake_analyze(image_bytes, serial, socket_hint=""):
        return AIResult(
            status="PASS",
            defect_count=0,
            confidence=0.95,
            observations="ok",
            socket_type_detected="INTEL_LGA1151",
            socket_bbox=[0.2, 0.2, 0.8, 0.8],
            crop_base64=base64.b64encode(crop).decode("ascii"),
        )

    monkeypatch.setattr(ai_proxy_service, "analyze_socket", fake_analyze)
    r = await client.post(POST, files=_files(), data=_data("SERD1", "CROP-01"))
    assert r.status_code == 200
    body = r.json()
    assert body["socket_bbox"] == [0.2, 0.2, 0.8, 0.8]
    assert body["socket_crop_url"] is not None

    crop_resp = await client.get(body["socket_crop_url"])
    assert crop_resp.status_code == 200
    assert crop_resp.headers["content-type"] == "image/png"

    # El crop esta en disco como evidencia (03_socket_crop.png)
    folder = Path(body["evidence_path"])
    assert (folder / "03_socket_crop.png").exists()
    assert (folder / "02_annotated.png").exists()


async def test_response_without_crop_has_null_crop_url(client, storage_dir, mock_ai_pass):
    r = await client.post(POST, files=_files(), data=_data("SERE1", "NOCROP-01"))
    assert r.status_code == 200
    body = r.json()
    assert body["socket_bbox"] is None
    assert body["socket_crop_url"] is None
