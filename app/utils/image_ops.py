"""Operaciones de geometria de imagen (Pillow puro).

Modulo independiente del resto de la app (sin imports de app) para
evitar dependencias circulares entre ai_proxy / image_processor.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image

MAX_DIMENSION = 1600


def normalize_image(image_bytes: bytes, max_dim: int = MAX_DIMENSION) -> bytes:
    """Abre, convierte a RGB y redimensiona si excede max_dim. Devuelve PNG.

    Es el MARCO DE REFERENCIA: todas las coordenadas fraccionales
    (bbox del socket, bboxes de defectos) se expresan sobre esta imagen.
    """
    img = Image.open(BytesIO(image_bytes))
    img.load()
    if img.mode != "RGB":
        img = img.convert("RGB")
    if max(img.size) > max_dim:
        scale = max_dim / max(img.size)
        new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        img = img.resize(new_size, Image.LANCZOS)
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def crop_to_bbox(
    img: Image.Image, bbox: list[float], margin: float = 0.12
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Corta la region dada por una bbox (fracciones de la imagen) con margen.

    El margen incluye contexto (brackets de retencion, borde del campo de
    pines) sin salir de la imagen. Devuelve (imagen recortada, region en
    pixeles de la imagen original como (x1, y1, x2, y2)).
    """
    x1 = min(1.0, max(0.0, float(bbox[0])))
    y1 = min(1.0, max(0.0, float(bbox[1])))
    x2 = min(1.0, max(0.0, float(bbox[2])))
    y2 = min(1.0, max(0.0, float(bbox[3])))
    x1, x2 = min(x1, x2), max(x1, x2)
    y1, y2 = min(y1, y2), max(y1, y2)

    px1, py1 = int(x1 * img.width), int(y1 * img.height)
    px2, py2 = int(x2 * img.width), int(y2 * img.height)
    dx = max(1.0, (px2 - px1) * margin)
    dy = max(1.0, (py2 - py1) * margin)
    cx1 = max(0, int(px1 - dx))
    cy1 = max(0, int(py1 - dy))
    cx2 = min(img.width, int(px2 + dx))
    cy2 = min(img.height, int(py2 + dy))

    # Region demasiado pequena: no aporta pines extra, se usa la imagen completa
    if cx2 - cx1 < 8 or cy2 - cy1 < 8:
        cx1, cy1, cx2, cy2 = 0, 0, img.width, img.height

    return img.crop((cx1, cy1, cx2, cy2)), (cx1, cy1, cx2, cy2)


def remap_bbox_frac(
    bbox_frac: list[float],
    crop_box: tuple[int, int, int, int],
    full_size: tuple[int, int],
) -> list[float]:
    """Reexpresa una bbox (fracciones del CROP) en fracciones de la imagen completa.

    Permite dibujar los defectos sobre la imagen original anotada:
    el modelo los da en coordenadas del crop analizado.
    """
    cx1, cy1, cx2, cy2 = crop_box
    crop_w = max(1, cx2 - cx1)
    crop_h = max(1, cy2 - cy1)
    full_w, full_h = full_size

    fx1 = (cx1 + float(bbox_frac[0]) * crop_w) / full_w
    fy1 = (cy1 + float(bbox_frac[1]) * crop_h) / full_h
    fx2 = (cx1 + float(bbox_frac[2]) * crop_w) / full_w
    fy2 = (cy1 + float(bbox_frac[3]) * crop_h) / full_h
    fx1, fx2 = min(fx1, fx2), max(fx1, fx2)
    fy1, fy2 = min(fy1, fy2), max(fy1, fy2)

    return [
        min(1.0, max(0.0, fx1)),
        min(1.0, max(0.0, fy1)),
        min(1.0, max(0.0, fx2)),
        min(1.0, max(0.0, fy2)),
    ]


def full_to_crop_frac(
    bbox_frac: list[float],
    crop_box: tuple[int, int, int, int],
    full_size: tuple[int, int],
) -> list[float]:
    """Reexpresa una bbox (fracciones de la imagen completa) en fracciones del CROP.

    Inversa de `remap_bbox_frac`: el VLM solo ve el crop, asi que las cajas de
    defectos localizadas sobre la imagen completa deben expresarse en su
    sistema de coordenadas antes de incluirlas en el prompt.
    """
    cx1, cy1, cx2, cy2 = crop_box
    crop_w = max(1, cx2 - cx1)
    crop_h = max(1, cy2 - cy1)
    full_w, full_h = full_size

    fx1 = (float(bbox_frac[0]) * full_w - cx1) / crop_w
    fy1 = (float(bbox_frac[1]) * full_h - cy1) / crop_h
    fx2 = (float(bbox_frac[2]) * full_w - cx1) / crop_w
    fy2 = (float(bbox_frac[3]) * full_h - cy1) / crop_h
    fx1, fx2 = min(fx1, fx2), max(fx1, fx2)
    fy1, fy2 = min(fy1, fy2), max(fy1, fy2)

    return [
        min(1.0, max(0.0, fx1)),
        min(1.0, max(0.0, fy1)),
        min(1.0, max(0.0, fx2)),
        min(1.0, max(0.0, fy2)),
    ]
