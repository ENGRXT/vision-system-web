"""Tests de operaciones de geometria de imagen (crop, remapeo, normalizacion)."""
from __future__ import annotations

from io import BytesIO

from PIL import Image

from app.utils.image_ops import (
    crop_to_bbox,
    full_to_crop_frac,
    normalize_image,
    remap_bbox_frac,
)


def test_normalize_image_resizes_and_returns_png():
    img = Image.new("RGB", (3000, 2000), (10, 20, 30))
    buf = BytesIO()
    img.save(buf, format="PNG")

    out = normalize_image(buf.getvalue())

    result = Image.open(BytesIO(out))
    assert result.format == "PNG"
    assert result.mode == "RGB"
    assert max(result.size) <= 1600
    # La proporcion se conserva
    assert abs(result.width / result.height - 3000 / 2000) < 0.01


def test_normalize_image_keeps_small_images():
    img = Image.new("RGB", (100, 80), (5, 5, 5))
    buf = BytesIO()
    img.save(buf, format="PNG")
    out = normalize_image(buf.getvalue())
    result = Image.open(BytesIO(out))
    assert result.size == (100, 80)


def test_crop_to_bbox_adds_margin_and_clamps():
    img = Image.new("RGB", (100, 100), (0, 0, 0))
    crop, box = crop_to_bbox(img, [0.2, 0.2, 0.8, 0.8])

    # El recorte debe ser MAYOR que la bbox por el margen
    assert box[0] < 20 and box[1] < 20
    assert box[2] > 80 and box[3] > 80
    assert crop.size == (box[2] - box[0], box[3] - box[1])


def test_crop_to_bbox_clamps_at_image_edges():
    img = Image.new("RGB", (100, 100), (0, 0, 0))
    crop, box = crop_to_bbox(img, [0.0, 0.0, 0.1, 0.1])
    # Nunca sale de la imagen
    assert box[0] >= 0 and box[1] >= 0
    assert box[2] <= 100 and box[3] <= 100


def test_crop_to_bbox_tiny_region_uses_full_image():
    img = Image.new("RGB", (100, 100), (0, 0, 0))
    crop, box = crop_to_bbox(img, [0.5, 0.5, 0.501, 0.501])
    assert box == (0, 0, 100, 100)


def test_remap_bbox_frac_identity_inside_crop():
    # Crop que ocupa el cuadrante inferior derecho de una imagen 200x200:
    # todo el crop (0..1, 0..1) debe mapear a [0.5..1.0, 0.5..1.0]
    out = remap_bbox_frac([0.0, 0.0, 1.0, 1.0], (100, 100, 200, 200), (200, 200))
    assert out == [0.5, 0.5, 1.0, 1.0]


def test_remap_bbox_frac_point():
    # Un punto en el centro del crop (0.5, 0.5) -> centro del cuadrante (0.75, 0.75)
    out = remap_bbox_frac([0.5, 0.5, 0.51, 0.51], (100, 100, 200, 200), (200, 200))
    assert abs(out[0] - 0.75) < 0.001
    assert abs(out[1] - 0.75) < 0.001


def test_remap_bbox_frac_clamps_to_unit():
    # Coordenadas extremas no salen de [0, 1]
    out = remap_bbox_frac([0.0, 0.0, 1.0, 1.0], (0, 0, 100, 100), (200, 200))
    assert out == [0.0, 0.0, 0.5, 0.5]


def test_full_to_crop_frac_point():
    # El centro de la imagen completa (0.5, 0.5 en 200x200 = pixel 100) es
    # el centro del crop (50, 50, 150, 150) -> (0.5, 0.5) en fracciones del crop
    out = full_to_crop_frac([0.5, 0.5, 0.51, 0.51], (50, 50, 150, 150), (200, 200))
    assert abs(out[0] - 0.5) < 0.001
    assert abs(out[1] - 0.5) < 0.001
    assert abs(out[2] - 0.52) < 0.001
    assert abs(out[3] - 0.52) < 0.001


def test_full_to_crop_frac_roundtrip_with_remap():
    # Imagen completa -> crop -> imagen completa: la caja se conserva
    crop_box = (132, 100, 625, 610)
    full_size = (756, 756)
    box = [0.3, 0.4, 0.35, 0.5]
    in_crop = full_to_crop_frac(box, crop_box, full_size)
    back = remap_bbox_frac(in_crop, crop_box, full_size)
    for got, exp in zip(back, box):
        assert abs(got - exp) < 0.002


def test_full_to_crop_frac_clamps_to_unit():
    # La caja completa sale del crop: se recorta a [0, 1]
    out = full_to_crop_frac([0.0, 0.0, 0.5, 0.5], (0, 0, 50, 50), (200, 200))
    assert out == [0.0, 0.0, 1.0, 1.0]
