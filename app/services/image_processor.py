"""Procesamiento de imagenes: base64, resize, anotaciones (Pillow)."""
from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from app.services.ai_proxy import AIResult

MAX_DIMENSION = 1600


def encode_image_base64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("ascii")


def normalize_image(image_bytes: bytes, max_dim: int = MAX_DIMENSION) -> bytes:
    """Abre, convierte a RGB y redimensiona si excede max_dim. Devuelve PNG."""
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


def annotate_image(image_bytes: bytes, ai_result: AIResult) -> bytes:
    """Genera la imagen anotada: banner de resultado + lista de defectos."""
    img = Image.open(BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")

    banner_h = 60
    if ai_result.status == "FAIL":
        color = (255, 0, 0, 200)
        text = f"FAIL - {ai_result.defect_count} defectos"
    elif ai_result.status == "PASS":
        color = (0, 200, 0, 200)
        text = "PASS"
    else:
        color = (255, 170, 0, 200)
        text = f"UNCERTAIN - confiar: {ai_result.confidence:.2f}"

    draw.rectangle([0, 0, img.width, banner_h], fill=color)
    font = _font(30)
    draw.text((10, 14), text, fill=(255, 255, 255, 255), font=font)

    if ai_result.defect_locations:
        small = _font(20)
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
