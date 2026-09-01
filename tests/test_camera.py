"""Tests del procesamiento de imagenes capturadas (image_processor)."""
from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

from app.services.ai_proxy import AIResult
from app.services.image_processor import (
    MAX_DIMENSION,
    annotate_image,
    encode_image_base64,
    normalize_image,
)


def _png(width: int, height: int, color=(120, 120, 120), mode="RGB") -> bytes:
    img = Image.new(mode, (width, height), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_encode_image_base64_roundtrip():
    raw = _png(10, 10)
    encoded = encode_image_base64(raw)
    assert base64.b64decode(encoded) == raw


def test_normalize_image_downscales_large_image():
    raw = _png(4000, 3000)
    result = normalize_image(raw)
    img = Image.open(BytesIO(result))
    assert img.format == "PNG"
    assert max(img.size) <= MAX_DIMENSION
    assert img.mode == "RGB"


def test_normalize_image_converts_rgba_to_rgb():
    raw = _png(64, 48, (255, 0, 0, 128), mode="RGBA")
    img = Image.open(BytesIO(normalize_image(raw)))
    assert img.mode == "RGB"


def test_normalize_image_keeps_small_image():
    raw = _png(100, 80)
    img = Image.open(BytesIO(normalize_image(raw)))
    assert img.size == (100, 80)


def test_annotate_image_fail_adds_banner():
    raw = _png(320, 240)
    result = AIResult(
        status="FAIL",
        defect_count=2,
        defect_locations=["Pin 12", "B4"],
        confidence=0.9,
        observations="x",
        socket_type_detected="AMD_AM5",
    )
    out = annotate_image(raw, result)
    assert out != raw
    img = Image.open(BytesIO(out))
    assert img.size == (320, 240)


def test_annotate_image_pass():
    raw = _png(320, 240)
    result = AIResult(
        status="PASS",
        defect_count=0,
        defect_locations=[],
        confidence=0.99,
        observations="ok",
        socket_type_detected="INTEL_LGA1700",
    )
    out = annotate_image(raw, result)
    img = Image.open(BytesIO(out))
    assert img.size == (320, 240)


def test_annotate_image_uncertain():
    raw = _png(320, 240)
    result = AIResult(
        status="UNCERTAIN",
        defect_count=0,
        defect_locations=[],
        confidence=0.5,
        observations="revisar",
        socket_type_detected="INTEL_LGA1700",
    )
    out = annotate_image(raw, result)
    assert out != raw
