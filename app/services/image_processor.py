"""Procesamiento de imagenes: base64 y anotaciones (Pillow)."""
from __future__ import annotations

import base64
from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from app.services.ai_proxy import AIResult
from app.utils.image_ops import MAX_DIMENSION, normalize_image  # noqa: F401 (re-export)


def encode_image_base64(image_bytes: bytes) -> str:
    """Convierte bytes de imagen a base64 (util para previews y tests)."""
    return base64.b64encode(image_bytes).decode("ascii")


def _font(size: int = 28) -> ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default()
    except Exception:  # pragma: no cover
        return ImageFont.ImageFont(BytesIO(b""))


def annotate_image(
    image_bytes: bytes,
    ai_result: AIResult,
    socket_bbox: Optional[list[float]] = None,
) -> bytes:
    """Genera la imagen anotada: banner, caja verde del socket (donde se
    enfoco la IA), cajas rojas numeradas por defecto y lista de texto."""
    img = Image.open(BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")

    banner_h = 60
    banner = f"{ai_result.status} - {ai_result.defect_count} defectos"
    if ai_result.pin_count is not None:
        banner += f" | pines: {ai_result.pin_count}"
    if ai_result.status == "FAIL":
        color = (255, 0, 0, 200)
        text = banner
    elif ai_result.status == "PASS":
        color = (0, 200, 0, 200)
        text = f"PASS | pines: {ai_result.pin_count}" if ai_result.pin_count is not None else "PASS"
    else:
        color = (255, 170, 0, 200)
        text = f"UNCERTAIN - confianza: {ai_result.confidence:.2f}"

    draw.rectangle([0, 0, img.width, banner_h], fill=color)
    font = _font(30)
    draw.text((10, 14), text, fill=(255, 255, 255, 255), font=font)
    small = _font(20)

    # Caja verde: la region del socket donde se enfoco la IA (prueba visual)
    if socket_bbox:
        sx1 = int(socket_bbox[0] * img.width)
        sy1 = int(socket_bbox[1] * img.height)
        sx2 = int(socket_bbox[2] * img.width)
        sy2 = int(socket_bbox[3] * img.height)
        draw.rectangle([sx1, sy1, sx2, sy2], outline=(0, 255, 120, 255), width=4)
        draw.rectangle([sx1, sy1, sx1 + 110, sy1 + 26], fill=(0, 160, 80, 230))
        draw.text((sx1 + 6, sy1 + 3), "SOCKET", fill=(255, 255, 255, 255), font=small)

    # Cajas rojas numeradas sobre cada pin defectuoso (bbox en fracciones de la imagen)
    for i, defect in enumerate(ai_result.defects[:10], start=1):
        if not defect.bbox:
            continue
        x1 = int(defect.bbox[0] * img.width)
        y1 = int(defect.bbox[1] * img.height)
        x2 = int(defect.bbox[2] * img.width)
        y2 = int(defect.bbox[3] * img.height)
        draw.rectangle([x1, y1, x2, y2], outline=(255, 40, 40, 255), width=3)
        label_y = max(0, y1 - 28)
        draw.rectangle([x1, label_y, x1 + 26, label_y + 24], fill=(255, 40, 40, 230))
        draw.text((x1 + 7, label_y + 2), str(i), fill=(255, 255, 255, 255), font=small)

    if ai_result.defect_locations:
        y = banner_h + 10
        for i, loc in enumerate(ai_result.defect_locations[:8], start=1):
            label = f"{i}. {loc}"
            box_w = 16 + 12 * len(label)
            draw.rectangle([10, y, 10 + box_w, y + 26], fill=(0, 0, 0, 160))
            draw.text((16, y + 3), label, fill=(255, 255, 255, 255), font=small)
            y += 32

    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
