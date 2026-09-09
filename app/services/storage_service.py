"""Almacenamiento centralizado de evidencia en el servidor.

Estructura:
  <base>/MES/01-15-2026/SERIAL_01152026/
       01_original.png
       02_annotated.png
       03_socket_crop.png   (solo si el pipeline corto el socket)
       metadata.json
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from app.config import settings
from app.services.ai_proxy import AIResult
from app.services.image_processor import annotate_image, normalize_image
from app.utils.validators import is_safe_path_component

SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class StorageService:
    def __init__(self) -> None:
        self.base_path = Path(settings.storage_base_path)
        try:
            self.base_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(f"Storage path no creable aun ({exc}); se creara en runtime")

    def get_inspection_folder(self, serial: str, timestamp: datetime) -> Path:
        """Crea la carpeta de evidencia con la nomenclatura del sistema.

        La creacion es atomica (mkdir sin exist_ok) para que dos
        inspecciones SIMULTANEAS del mismo serial no compartan carpeta
        y se pisen los archivos de evidencia entre estaciones.
        """
        month_name = timestamp.strftime("%B").upper()
        date_folder = timestamp.strftime("%m-%d-%Y")
        serial_folder = f"{serial}_{timestamp.strftime('%m%d%Y')}"
        base = self.base_path / month_name / date_folder
        for counter in range(100):
            name = serial_folder if counter == 0 else f"{serial_folder}_{counter:03d}"
            path = base / name
            try:
                # Sin exist_ok: si ya existe (inspeccion concurrente),
                # FileExistsError y probamos el siguiente contador
                path.mkdir(parents=True)
            except FileExistsError:
                continue
            return path
        raise RuntimeError(f"Imposible crear carpeta de evidencia para {serial}")

    def save_inspection(
        self,
        serial: str,
        image_bytes: bytes,
        ai_result: AIResult,
        metadata: dict,
        timestamp: Optional[datetime] = None,
    ) -> Path:
        # UTC siempre: coherente con el timestamp de la inspeccion
        # (evita descuadre de carpeta entre URL y disco en TZ != UTC).
        if timestamp is None or timestamp.tzinfo is None:
            timestamp = datetime.now(timezone.utc)
        else:
            timestamp = timestamp.astimezone(timezone.utc)
        folder = self.get_inspection_folder(serial, timestamp)
        original_path = folder / "01_original.png"
        normalized = normalize_image(image_bytes)
        original_path.write_bytes(normalized)

        annotated_path = folder / "02_annotated.png"
        annotated = annotate_image(normalized, ai_result, socket_bbox=ai_result.socket_bbox)
        annotated_path.write_bytes(annotated)

        # Evidencia extra: el recorte donde se enfoco la IA (solo si hay crop)
        if ai_result.crop_base64:
            crop_path = folder / "03_socket_crop.png"
            crop_path.write_bytes(base64.b64decode(ai_result.crop_base64))

        # Evidencia de la medicion CV: los pines exactos que midio el sistema
        # (solo si hubo medicion CV)
        if ai_result.cv_analysis_base64:
            cv_path = folder / "04_cv_analysis.png"
            cv_path.write_bytes(base64.b64decode(ai_result.cv_analysis_base64))

        meta_path = folder / "metadata.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False, default=str)

        logger.info(f"Evidence saved to {folder}")
        return folder

    # ------------------------------------------------------------------ #
    # Listado y resolucion de archivos (router /api/evidence)
    # ------------------------------------------------------------------ #

    def list_evidence(
        self, month: Optional[str] = None, date: Optional[str] = None
    ) -> dict:
        month = (month or "").upper()
        date = date or ""
        if month and not is_safe_path_component(month):
            raise ValueError("month invalido")
        if date and not is_safe_path_component(date):
            raise ValueError("date invalido")

        base = self.base_path
        if not month:
            months = sorted(
                p.name for p in base.iterdir() if p.is_dir()
            ) if base.exists() else []
            return {"months": months}

        month_dir = base / month
        if not month_dir.exists():
            return {"month": month, "dates": []}

        if not date:
            dates = sorted(p.name for p in month_dir.iterdir() if p.is_dir())
            return {"month": month, "dates": dates}

        date_dir = month_dir / date
        if not date_dir.exists():
            return {"month": month, "date": date, "inspections": []}

        inspections = []
        for folder in sorted(p for p in date_dir.iterdir() if p.is_dir()):
            files = sorted(f.name for f in folder.iterdir() if f.is_file())
            inspections.append({"folder": folder.name, "files": files})
        return {"month": month, "date": date, "inspections": inspections}

    def resolve_file(
        self, month: str, date: str, folder: str, filename: str
    ) -> Optional[Path]:
        parts = (month.upper(), date, folder, filename)
        if not all(is_safe_path_component(p) for p in parts):
            return None
        base = self.base_path.resolve()
        path = (base / parts[0] / parts[1] / parts[2] / parts[3]).resolve()
        if base not in path.parents:
            return None
        if not path.is_file():
            return None
        return path


storage_service = StorageService()
