"""Tests del modulo de vision artificial determinista (socket_cv).

Las imagenes sinteticas son reticulas PERFECTAS, por lo que el CV debe
medirlas EXACTO (sin tolerancias de conteo): es la razon de ser del
pipeline hibrido (el VLM "estima", el CV mide).
"""
from __future__ import annotations

from io import BytesIO

import cv2
import numpy as np
import pytest
from PIL import Image

from app.utils import socket_cv


def make_grid(
    rows: int,
    cols: int,
    spacing: int = 24,
    pin_r: int = 10,
    margin: int = 150,
    missing: set[tuple[int, int]] | None = None,
    bent: set[tuple[int, int]] | None = None,
    lean: set[tuple[int, int]] | None = None,
    bg: int = 40,
) -> Image.Image:
    """Placa sintetica: reticula de pines brillantes sobre fondo oscuro.

    - missing: celda SIN pin (celda vacia).
    - bent: pin base + "cabeza" doblada unida hacia la celda de arriba
      (queda un solo componente = pin deformado; usar spacing amplio
      para que la cabeza no toque a los vecinos).
    - lean: pin normal + astilla fina pegada junto a el (componente
      anormal en celda ocupada = pin doblado).
    """
    missing = missing or set()
    bent = bent or set()
    lean = lean or set()
    w = margin * 2 + (cols - 1) * spacing
    h = margin * 2 + (rows - 1) * spacing
    img = np.full((h, w, 3), bg, dtype=np.uint8)
    for r in range(rows):
        for c in range(cols):
            x = margin + c * spacing
            y = margin + r * spacing
            if (r, c) in missing:
                continue
            cv2.circle(img, (x, y), pin_r, (235, 235, 235), -1)
    for (r, c) in bent:
        x = margin + c * spacing
        y = margin + r * spacing
        # "Cabeza" doblada PEGADA a la base (misma celda): el centroide del
        # componente cae dentro de su celda (si la cabeza llegara a la fila de
        # arriba, el argmin la mapearia a una celda vecina ya ocupada y
        # generaria un "faltante" falso en la celda real).
        d = max(3, int(pin_r * 1.5))
        hx, hy = x + d, y - d
        cv2.line(img, (x, y), (hx, hy), (235, 235, 235), 5)
        cv2.circle(img, (hx, hy), pin_r, (235, 235, 235), -1)
    for (r, c) in lean:
        x = margin + c * spacing
        y = margin + r * spacing
        # Astilla SEPARADA del pin sano (hueco > nucleo de cierre 3x3) y
        # pequena (area < 0.25*mediana) para que quede como componente
        # anormal en celda ocupada (pin doblado saliendo de su posicion).
        gap = pin_r + 5
        cv2.line(img, (x + gap, y), (x + gap + 3, y), (235, 235, 235), 3)
    return Image.fromarray(img)


@pytest.mark.parametrize(
    "count,expected_type,expected_conf",
    [
        (1151, "INTEL_LGA1151", 0.97),
        (1156, "INTEL_LGA1151", 0.97),  # reticula 34x34 completa: diff 5
        (1200, "INTEL_LGA1200", 0.97),
        (1180, "INTEL_LGA1200", 0.75),  # diff 20 a 1200
        (1700, "INTEL_LGA1700", 0.97),
        (1718, "AMD_AM5", 0.97),
        (784, "UNKNOWN", 0.3),  # lejos de toda referencia
        (0, "UNKNOWN", 0.0),
    ],
)
def test_match_socket_type(count, expected_type, expected_conf):
    assert socket_cv.match_socket_type(count) == (expected_type, expected_conf)


def test_find_socket_locates_grid():
    det = socket_cv.find_socket(make_grid(34, 34))
    assert det is not None
    assert 1140 <= det.pin_estimate <= 1170
    assert socket_cv.match_socket_type(det.pin_estimate)[0] == "INTEL_LGA1151"
    # Bbox centrada y ajustada al campo de pines (no a toda la imagen)
    assert 0.05 < det.bbox_frac[0] < 0.3
    assert 0.05 < det.bbox_frac[1] < 0.3
    assert 0.7 < det.bbox_frac[2] < 0.95
    assert 0.7 < det.bbox_frac[3] < 0.95
    assert det.confidence > 0.5


def test_find_socket_ignores_distractor():
    board = make_grid(28, 28)
    canvas = np.full(
        (board.height + 200, board.width * 2 + 200, 3), 40, dtype=np.uint8
    )
    canvas[: board.height, : board.width] = np.asarray(board)
    # Distractor a la derecha: "capacitores" (puntos brillantes sueltos,
    # sin densidad de socket)
    for r in range(15):
        for c in range(15):
            cv2.circle(
                canvas,
                (board.width + 300 + c * 30, 150 + r * 30),
                4,
                (235, 235, 235),
                -1,
            )
    det = socket_cv.find_socket(Image.fromarray(canvas))
    assert det is not None
    assert 770 <= det.pin_estimate <= 800
    # El bbox debe caer en la mitad IZQUIERDA (el campo real), no en el
    # distractor
    assert (det.bbox_frac[0] + det.bbox_frac[2]) / 2 < 0.5


def test_find_socket_flat_image_returns_none():
    img = Image.new("RGB", (640, 480), (90, 95, 100))
    assert socket_cv.find_socket(img) is None


def test_analyze_pins_counts_exact_grid():
    a = socket_cv.analyze_pins(make_grid(34, 34))
    assert a is not None
    assert a.pin_count == 1156
    assert a.socket_type == "INTEL_LGA1151"
    assert a.type_confidence == 0.97
    assert a.defects == []
    assert (a.grid_rows, a.grid_cols) == (34, 34)
    assert a.confidence > 0.9


def test_analyze_pins_missing_pin():
    a = socket_cv.analyze_pins(make_grid(20, 20, missing={(10, 10)}))
    assert a is not None
    assert a.pin_count == 399
    assert len(a.defects) == 1
    d = a.defects[0]
    assert "faltante" in d.description
    # La caja cae sobre la celda (10, 10), en el centro de la reticula
    assert abs((d.bbox_frac[0] + d.bbox_frac[2]) / 2 - 0.5) < 0.05
    assert abs((d.bbox_frac[1] + d.bbox_frac[3]) / 2 - 0.5) < 0.05


def test_analyze_pins_bent_pin_counted_once():
    # spacing amplio: la "cabeza" doblada no toca a los pines vecinos
    a = socket_cv.analyze_pins(make_grid(20, 20, spacing=40, pin_r=8, bent={(10, 10)}))
    assert a is not None
    # El pin doblado se cuenta UNA vez (ni falta ni se duplica)
    assert a.pin_count == 400
    assert len(a.defects) == 1
    desc = a.defects[0].description
    assert "deformado" in desc or "doblado" in desc


def test_analyze_pins_lean_pin_reported_as_bent():
    # Astilla fina junto a un pin sano: la celda YA esta ocupada, por lo
    # que el componente anormal es el pin salido de su posicion
    a = socket_cv.analyze_pins(make_grid(20, 20, spacing=40, pin_r=8, lean={(10, 10)}))
    assert a is not None
    assert a.pin_count == 400
    assert len(a.defects) == 1
    assert "doblado" in a.defects[0].description


def test_analyze_pins_flat_returns_none():
    img = Image.new("RGB", (512, 512), (90, 95, 100))
    assert socket_cv.analyze_pins(img) is None


def test_analyze_pins_too_few_pins_returns_none():
    # 8x8 = 64 pines < MIN_CONFIDENT_PINS (80): medicion no confiable
    assert socket_cv.analyze_pins(make_grid(8, 8, margin=120)) is None


def test_render_analysis_produces_png():
    grid = make_grid(20, 20)
    a = socket_cv.analyze_pins(grid)
    assert a is not None
    png = socket_cv.render_analysis(grid, a)
    img = Image.open(BytesIO(png))
    assert img.format == "PNG"
    assert min(img.size) > 0
