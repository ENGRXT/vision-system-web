"""Configuracion de logging con Loguru (consola + archivo rotado)."""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

_configured = False


def setup_logger(level: str | None = None, log_file: str | None = None) -> None:
    global _configured
    from app.config import settings

    logger.remove()
    logger.add(
        sys.stderr,
        level=level or settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    log_dir = Path(log_file).parent if log_file else None
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            logger.add(
                str(log_dir / "app.log"),
                level=level or settings.log_level,
                rotation="50 MB",
                retention="14 days",
                encoding="utf-8",
                enqueue=True,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning(f"No se pudo configurar log file: {exc}")
    _configured = True


def get_logger():
    if not _configured:
        setup_logger()
    return logger
