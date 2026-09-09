"""Tests del almacenamiento de evidencia (storage_service)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO

import pytest
from PIL import Image

from app.services.ai_proxy import AIResult
from app.services.storage_service import storage_service


def _png() -> bytes:
    img = Image.new("RGB", (320, 240), (90, 90, 90))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _ai_result(status: str = "PASS") -> AIResult:
    return AIResult(
        status=status,
        defect_count=1 if status == "FAIL" else 0,
        defect_locations=["Pin 5"] if status == "FAIL" else [],
        confidence=0.95,
        observations="test",
        socket_type_detected="INTEL_LGA1700",
    )


@pytest.fixture
def svc(storage_dir):
    return storage_service


def test_save_inspection_creates_folder_structure(svc):
    now = datetime(2026, 8, 31, 10, 30, 0, tzinfo=timezone.utc)
    folder = svc.save_inspection(
        "TEST1234", _png(), _ai_result(),
        {"inspection_id": "x", "serial": "TEST1234"},
        timestamp=now,
    )
    month = now.strftime("%B").upper()
    date = now.strftime("%m-%d-%Y")
    expected = svc.base_path / month / date / f"TEST1234_{now.strftime('%m%d%Y')}"
    assert folder == expected
    assert (folder / "01_original.png").is_file()
    assert (folder / "02_annotated.png").is_file()
    meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert meta["serial"] == "TEST1234"


def test_save_inspection_duplicate_serial_gets_counter(svc):
    first = svc.save_inspection("DUP001", _png(), _ai_result(), {})
    second = svc.save_inspection("DUP001", _png(), _ai_result(), {})
    assert first != second
    assert second.name == first.name + "_001"
    assert (second / "01_original.png").is_file()


def test_original_image_is_valid_png(svc):
    folder = svc.save_inspection("PNG001", _png(), _ai_result(), {})
    img = Image.open(folder / "01_original.png")
    assert img.format == "PNG"
    img2 = Image.open(folder / "02_annotated.png")
    assert img2.format == "PNG"


def test_list_evidence_hierarchy(svc):
    svc.save_inspection("LIST01", _png(), _ai_result(), {})

    months = svc.list_evidence()
    now_month = datetime.now(timezone.utc).strftime("%B").upper()
    assert now_month in months["months"]

    dates = svc.list_evidence(month=now_month)
    now_date = datetime.now(timezone.utc).strftime("%m-%d-%Y")
    assert now_date in dates["dates"]

    inspections = svc.list_evidence(month=now_month, date=now_date)
    folders = [i["folder"] for i in inspections["inspections"]]
    assert any(f.startswith("LIST01_") for f in folders)
    entry = next(i for i in inspections["inspections"] if i["folder"].startswith("LIST01_"))
    assert set(entry["files"]) == {"01_original.png", "02_annotated.png", "metadata.json"}


def test_list_evidence_rejects_path_traversal(svc):
    with pytest.raises(ValueError):
        svc.list_evidence(month="../etc")
    with pytest.raises(ValueError):
        svc.list_evidence(month="AUGUST", date="..%2F..")


def test_resolve_file_valid(svc):
    folder = svc.save_inspection("RES001", _png(), _ai_result(), {})
    now = datetime.now(timezone.utc)
    month = now.strftime("%B").upper()
    date = now.strftime("%m-%d-%Y")
    path = svc.resolve_file(month, date, folder.name, "metadata.json")
    assert path is not None
    assert path.is_file()


def test_resolve_file_blocks_traversal(svc):
    folder = svc.save_inspection("RES002", _png(), _ai_result(), {})
    now = datetime.now(timezone.utc)
    month = now.strftime("%B").upper()
    date = now.strftime("%m-%d-%Y")
    assert (
        svc.resolve_file(month, date, "../../etc", "passwd") is None
    )
    assert svc.resolve_file(month, date, folder.name, "../01_original.png") is None
    assert svc.resolve_file(month, date, "NO_EXISTE", "a.png") is None


def test_concurrent_same_serial_gets_distinct_folders(svc):
    """Creaciones SIMULTANEAS del mismo serial (2 estaciones a la vez)
    deben dar carpetas distintas, nunca compartidas."""
    import threading

    now = datetime(2026, 2, 9, 9, 2, 0, tzinfo=timezone.utc)
    folders: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker():
        # Barrera para maximizar la concurrencia de la creacion
        barrier.wait()
        folder = svc.get_inspection_folder("RACE01", now)
        with lock:
            folders.append(folder)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(folders) == 8
    assert len(set(folders)) == 8, f"Carpetas compartidas: {folders}"
