"""Validaciones de dominio (serial, tipos de socket, MIME)."""
from __future__ import annotations

import re

SERIAL_REGEX = re.compile(r"^[A-Z0-9]{4,50}$")

SOCKET_TYPES = ("INTEL_LGA1700", "AMD_AM5", "AUTO")

ALLOWED_IMAGE_MIME = ("image/png", "image/jpeg", "image/webp")

MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB (coherente con client_max_body_size nginx)

DISPOSITIONS = ("PASS", "FAIL")


def is_valid_serial(serial: str) -> bool:
    return bool(serial) and bool(SERIAL_REGEX.match(serial.strip().upper()))


def is_valid_socket_type(value: str) -> bool:
    return value.upper() in SOCKET_TYPES


def is_allowed_image(content_type: str | None) -> bool:
    return (content_type or "").lower() in ALLOWED_IMAGE_MIME


def is_safe_path_component(value: str) -> bool:
    """Componente de ruta de evidencia: alfanumerico + _ - . (sin '/' ni '..')."""
    return bool(re.fullmatch(r"[A-Za-z0-9._-]{1,100}", value))
