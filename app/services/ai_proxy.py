"""Proxy hacia el modelo VLM local (vLLM, API OpenAI-compatible).

PIPELINE HIBRIDO (CV determinista + verificacion VLM):
  0. NORMALIZAR: unico marco de referencia (PNG, max 1600 px).
  1. LOCALIZAR (CV): `socket_cv.find_socket` localiza el campo de pines por
     densidad (umbral + morfologia + componentes). Exacto y sin llamada al
     VLM. Si el CV no lo localiza con confianza, el VLM localiza (camino
     clasico).
  2. CROP + ZOOM (Pillow): margen 12%, ampliado si es pequeno.
   3. MEDIR (CV): `socket_cv.analyze_pins` sobre la imagen COMPLETA (la
      medicion se restringe a la region localizada; si no hay, la
      autodetecta por densidad) — conteo EXACTO de pines, tipo de socket por
      conteo de referencia, pines faltantes (celdas vacias con vecinos) y
      pines doblados (componentes fuera de la reticula). Las cajas de
      defecto salen en fracciones de la imagen completa.
    4. VERIFICAR (VLM): una llamada (sobre el crop ampliado) que confirma la
       medicion y anade solo defectos adicionales que vea con claridad. NUNCA
       modifica el conteo ni el tipo medidos (contandolos, el VLM estima y
       puede alucinar). En el prompt, los defectos medidos por el CV se
       expresan en fracciones del CROP (lo unico que ve el modelo). Los
       defectos que el VLM anade (fracciones del crop) se remapean a la
       imagen completa antes de la fusion.
   5. FUSION: el CV es autoritativo para conteo/tipo; defectos = CV + VLM
      (deduplicados por posicion).

Robustez:
  - Con medicion CV y VLM caido (circuit breaker o exception): devuelve el
    resultado solo-CV (detection_method="CV") y NO falla con 503.
  - Sin medicion CV (imagen sin socket medible): camino clasico solo-VLM
    (detection_method="VLM"); un fallo de la IA sigue bloqueando (503).

Incluye circuit breaker para manejo de fallos del servicio de IA y
notificacion en tiempo real (AI_SERVICE_DOWN / AI_SERVICE_RECOVERED).
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional

import httpx
from loguru import logger
from PIL import Image
from pydantic import BaseModel, Field

from app.config import (
    LOCATE_SOCKET_PROMPT,
    USER_PROMPT_CONFIRMED_TEMPLATE,
    USER_PROMPT_TEMPLATE,
    USER_PROMPT_VERIFY_TEMPLATE,
    settings,
)
from app.schemas.websocket import (
    EVENT_AI_SERVICE_DOWN,
    EVENT_AI_SERVICE_RECOVERED,
)
from app.services.websocket_manager import ws_manager
from app.utils import socket_cv
from app.utils.image_ops import (
    crop_to_bbox,
    full_to_crop_frac,
    normalize_image,
    remap_bbox_frac,
)


class DefectInfo(BaseModel):
    """Defecto individual: descripcion y caja delimitadora en la imagen."""

    description: str = ""
    # [x1, y1, x2, y2] como fracciones 0.0-1.0 del ancho/alto de la imagen
    # COMPLETA, origen en la esquina superior izquierda. None si el modelo
    # no la dio.
    bbox: Optional[list[float]] = None


class AIResult(BaseModel):
    status: str = Field(..., pattern="^(PASS|FAIL|UNCERTAIN)$")
    defect_count: int = Field(default=0, ge=0)
    defect_locations: list[str] = Field(default_factory=list)
    defects: list[DefectInfo] = Field(default_factory=list)
    pin_count: Optional[int] = Field(default=None, ge=0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    observations: str
    socket_type_detected: str
    # bbox de la region del socket que se analizo (fracciones de la imagen
    # completa). None si la localizacion fallo y se analizo la imagen completa.
    socket_bbox: Optional[list[float]] = None
    # Base64 del crop analizado (lo que vio el modelo en la etapa 3). Solo se
    # usa para guardar evidencia en disco; se excluye de la serializacion.
    crop_base64: Optional[str] = Field(default=None, exclude=True)
    # Metodo de deteccion: "HYBRID" (CV+VLM), "CV" (solo medicion, IA sin
    # servicio) o "VLM" (camino clasico cuando el CV no pudo medir)
    detection_method: Optional[str] = Field(default=None, pattern="^(HYBRID|CV|VLM)$")
    # Base64 del render de la medicion CV (pines contados en verde, defectos
    # en rojo): evidencia 04_cv_analysis.png. Excluida de la serializacion.
    cv_analysis_base64: Optional[str] = Field(default=None, exclude=True)


class AIProxyService:
    def __init__(self) -> None:
        self.circuit_open = False
        self._fail_streak = 0

    @property
    def base_url(self) -> str:
        return settings.ai_base_url.rstrip("/")

    @property
    def model_name(self) -> str:
        return settings.ai_model_name

    @property
    def timeout(self) -> float:
        return settings.ai_timeout_seconds

    @property
    def max_retries(self) -> int:
        return settings.ai_max_retries

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {settings.ai_api_key}"}

    async def list_models(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{self.base_url}/models", headers=self._headers())
            response.raise_for_status()
        return response.json().get("data", [])

    async def check_connection(self) -> tuple[bool, str, list[str]]:
        try:
            models = await self.list_models()
            names = [m.get("id", "") for m in models]
            return True, "OK", names
        except Exception as exc:
            return False, str(exc), []

    async def _chat(self, image_b64: str, system_prompt: str, user_text: str) -> str:
        """Llamada al VLM con reintentos y circuit breaker. Devuelve el texto.

        El circuit breaker protege AMBAS etapas: si la IA esta caida, la
        inspeccion falla rapido (503) en lugar de esperarse el timeout.
        """
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": user_text,
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_b64}"
                            },
                        },
                    ],
                },
            ],
            "max_tokens": settings.ai_max_tokens,
            "temperature": settings.ai_temperature,
        }

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=self._headers(),
                        json=payload,
                        timeout=self.timeout,
                    )
                    response.raise_for_status()

                choice = response.json()["choices"][0]
                message = choice.get("message") or {}
                content = message.get("content")
                if not content:
                    content = message.get("reasoning") or message.get(
                        "reasoning_content"
                    )
                if not content:
                    raise RuntimeError(
                        "vLLM devolvio respuesta vacia "
                        f"(finish_reason={choice.get('finish_reason')})"
                    )

                if self.circuit_open:
                    self.circuit_open = False
                    self._fail_streak = 0
                    # Estado del sistema: lo ven todas las estaciones
                    await ws_manager.broadcast(
                        EVENT_AI_SERVICE_RECOVERED,
                        {"timestamp": datetime.now(timezone.utc).isoformat()},
                        global_event=True,
                    )
                self._fail_streak = 0
                return content

            except Exception as exc:
                last_error = exc
                logger.warning(f"AI attempt {attempt + 1} failed: {exc}")
                if attempt < self.max_retries:
                    await asyncio.sleep(2**attempt)

        self.circuit_open = True
        self._fail_streak += 1
        # Estado del sistema: lo ven todas las estaciones
        await ws_manager.broadcast(
            EVENT_AI_SERVICE_DOWN,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error_message": str(last_error),
                "retry_count": self.max_retries,
            },
            global_event=True,
        )
        assert last_error is not None
        raise last_error

    async def analyze_socket(
        self,
        image_bytes: bytes,
        serial: str,
        socket_hint: str = "",
    ) -> AIResult:
        """Analiza el socket con el pipeline hibrido:
        CV determinista (medicion) + VLM (verificacion)."""
        # Etapa 0: un unico marco de referencia para crop / anotacion / coords
        normalized = normalize_image(image_bytes)
        norm_img = Image.open(BytesIO(normalized))
        norm_img.load()
        norm_b64 = base64.b64encode(normalized).decode("ascii")

        # Etapa 1 (CV): localizacion determinista del campo de pines. Corre
        # en hilo: cv2 es CPU-y-blocking y no debe detener el event loop.
        cv_detection = await asyncio.to_thread(socket_cv.find_socket, norm_img)
        socket_bbox: Optional[list[float]] = None
        if cv_detection is not None:
            socket_bbox = [round(v, 5) for v in cv_detection.bbox_frac]
            logger.info(
                f"Socket localizado por CV: {socket_bbox} "
                f"(~{cv_detection.pin_estimate} pines)"
            )

        # Estado del circuito ANTES de las llamadas VLM de esta inspeccion:
        # si ya estaba abierto (fallo de una inspeccion previa), la Etapa 4
        # no vuelve a llamar al VLM. Se captura aqui (no en la Etapa 4)
        # porque la propia localizacion VLM de ESTA llamada puede ser la que
        # abra el circuito; en ese caso el error original se propaga.
        circuit_was_open = self.circuit_open

        # Etapa 1 (VLM, fallback): si el CV no pudo localizar, lo hace el
        # modelo (camino clasico de 2 etapas). Un fallo NUNCA bloquea la
        # inspeccion: se degrada a imagen completa.
        if socket_bbox is None:
            try:
                loc_text = await self._chat(
                    norm_b64,
                    LOCATE_SOCKET_PROMPT,
                    f"Locate the CPU socket in this motherboard photo (serial {serial}).",
                )
                socket_bbox = self._parse_socket_bbox(loc_text)
            except Exception as exc:
                logger.warning(f"Localizacion de socket fallo, usando imagen completa: {exc}")
                socket_bbox = None

        # Etapa 2: crop + zoom (solo si se encontro una region valida)
        crop_box: Optional[tuple[int, int, int, int]] = None
        crop_b64: Optional[str] = None
        pre_zoom_crop: Optional[Image.Image] = None
        if socket_bbox is not None:
            pre_zoom_crop, crop_box = crop_to_bbox(norm_img, socket_bbox)
            # Zoom: los VLM ven mejor el detalle de pines con mas pixeles
            analyze_img: Image.Image = pre_zoom_crop
            longest = max(analyze_img.size)
            if longest < 1024:
                analyze_img = analyze_img.resize(
                    (analyze_img.width * 2, analyze_img.height * 2), Image.LANCZOS
                )
            elif longest > 2048:
                scale = 2048 / longest
                analyze_img = analyze_img.resize(
                    (
                        max(1, int(analyze_img.width * scale)),
                        max(1, int(analyze_img.height * scale)),
                    ),
                    Image.LANCZOS,
                )
            crop_buf = BytesIO()
            analyze_img.save(crop_buf, format="PNG")
            crop_b64 = base64.b64encode(crop_buf.getvalue()).decode("ascii")

        # Etapa 3 (CV): medicion determinista de la imagen COMPLETA,
        # restringida a la region localizada (CV o VLM); si no hay region, la
        # autodetecta por densidad. El crop + zoom de la Etapa 2 es solo para
        # que el VLM vea mejor el detalle, no para la medicion.
        pin_analysis: Optional[socket_cv.PinAnalysis] = await asyncio.to_thread(
            socket_cv.analyze_pins,
            norm_img,
            tuple(socket_bbox) if socket_bbox is not None else None,
        )
        if pin_analysis is not None:
            logger.info(
                f"Medicion CV: {pin_analysis.pin_count} pines -> "
                f"{pin_analysis.socket_type} (reticula "
                f"{pin_analysis.grid_rows}x{pin_analysis.grid_cols}), "
                f"{len(pin_analysis.defects)} defectos"
            )

        # Tipo a reportar: la medicion CV es autoritativa. Si no pudo
        # identificar (UNKNOWN) y el operador CONFIRMO un tipo, se usa el del
        # operador.
        hint = (socket_hint or "AUTO").upper()
        if pin_analysis is None:
            final_type = "UNKNOWN"
        elif hint != "AUTO" and pin_analysis.socket_type == "UNKNOWN":
            final_type = hint
        else:
            final_type = pin_analysis.socket_type

        # Etapa 4 (VLM): verificar la medicion (1 sola llamada) o, sin
        # medicion CV, el analisis clasico.
        if pin_analysis is not None:
            # El VLM solo ve el CROP: las cajas CV (fracciones de la imagen
            # completa) se expresan en fracciones del crop para que el modelo
            # encuentre esos defectos en la imagen que recibe.
            cv_defects_list = []
            for d in pin_analysis.defects:
                bbox = d.bbox_frac
                if bbox and crop_box is not None:
                    bbox = full_to_crop_frac(bbox, crop_box, norm_img.size)
                cv_defects_list.append({"description": d.description, "bbox": bbox})
            cv_defects_text = json.dumps(
                cv_defects_list or "sin defectos localizados por la medicion",
                ensure_ascii=False,
            )
            user_text = USER_PROMPT_VERIFY_TEMPLATE.format(
                serial=serial,
                pin_count=pin_analysis.pin_count,
                socket_type=final_type,
                cv_defects=cv_defects_text,
            )
        elif hint == "AUTO":
            user_text = USER_PROMPT_TEMPLATE.format(serial=serial)
        else:
            user_text = USER_PROMPT_CONFIRMED_TEMPLATE.format(
                serial=serial, socket_hint=hint
            )

        vlm_result: Optional[AIResult] = None
        if circuit_was_open:
            if pin_analysis is None:
                raise RuntimeError("Circuit breaker OPEN: AI service unavailable")
            logger.warning("Circuit breaker OPEN: se devuelve resultado solo CV")
        else:
            try:
                text = await self._chat(
                    crop_b64 if crop_b64 else norm_b64,
                    settings.ai.system_prompt,
                    user_text,
                )
                vlm_result = self._parse_response(text)
                # El VLM vio el CROP: sus bboxes (fracciones del crop) se
                # remapean a la imagen completa ANTES de la fusion. Los
                # defectos CV ya salen en coordenadas de imagen completa.
                if crop_box is not None:
                    for d in vlm_result.defects:
                        if d.bbox:
                            d.bbox = remap_bbox_frac(d.bbox, crop_box, norm_img.size)
            except Exception as exc:
                if pin_analysis is None:
                    raise
                logger.warning(f"Verificacion VLM fallo; resultado solo CV: {exc}")
                vlm_result = None

        # Etapa 5: fusion. El CV es autoritativo para conteo/tipo; los
        # defectos = CV + adicionales del VLM (deduplicados por posicion).
        if pin_analysis is not None:
            result = self._merge_cv_vlm(pin_analysis, final_type, vlm_result)
            # Evidencia de la medicion CV (04_cv_analysis.png): los pines
            # exactos que midio el sistema
            try:
                result.cv_analysis_base64 = base64.b64encode(
                    await asyncio.to_thread(
                        socket_cv.render_analysis, norm_img, pin_analysis
                    )
                ).decode("ascii")
            except Exception as exc:
                logger.warning(f"No se pudo renderizar la evidencia CV: {exc}")
        else:
            if vlm_result is None:
                raise RuntimeError("Sin medicion CV ni respuesta del VLM")
            result = vlm_result
            result.detection_method = "VLM"

        result.socket_bbox = socket_bbox
        result.crop_base64 = crop_b64
        return result

    def _merge_cv_vlm(
        self,
        cv: socket_cv.PinAnalysis,
        final_type: str,
        vlm: Optional[AIResult],
    ) -> AIResult:
        """Fusion de la medicion CV (autoritativa) con la verificacion VLM.

        - Conteo y tipo: siempre los del CV (medicion, no estimacion).
        - Defectos: los del CV + los adicionales del VLM (dedup por posicion).
        - Estado: FAIL si hay ALGUN defecto, PASS en caso contrario.
        - Sin VLM disponible: resultado solo CV (la inspeccion no se bloquea).
        """
        defects: list[DefectInfo] = [
            DefectInfo(description=d.description, bbox=list(d.bbox_frac))
            for d in cv.defects
        ]
        base_obs = (
            f"Medicion CV: {cv.pin_count} pines contados, socket identificado como "
            f"{final_type} (confianza {cv.type_confidence:.2f})."
        )

        if vlm is None:
            base_obs += (
                " Verificacion VLM no disponible (IA sin servicio); resultado "
                "solo por vision artificial."
            )
            return AIResult(
                status="FAIL" if defects else "PASS",
                defect_count=len(defects),
                defect_locations=[d.description for d in defects if d.description],
                defects=defects,
                pin_count=cv.pin_count,
                confidence=cv.confidence,
                observations=base_obs,
                socket_type_detected=final_type,
                detection_method="CV",
            )

        # Dedup: un defecto VLM cuyo centro cae dentro de una caja de
        # defecto CV (ampliada) es el MISMO pin.
        for d in vlm.defects:
            if d.bbox and any(
                self._bbox_overlap(d.bbox, cd.bbox) for cd in defects if cd.bbox
            ):
                continue
            defects.append(d)

        observations = base_obs + " " + (vlm.observations or "").strip()
        if vlm.socket_type_detected and vlm.socket_type_detected != final_type:
            observations += (
                f" Nota: el VLM indico {vlm.socket_type_detected}; se mantiene el "
                f"tipo medido por CV ({final_type})."
            )
        return AIResult(
            status="FAIL" if defects else "PASS",
            defect_count=len(defects),
            defect_locations=[d.description for d in defects if d.description],
            defects=defects,
            pin_count=cv.pin_count,
            confidence=round(0.6 * cv.confidence + 0.4 * vlm.confidence, 3),
            observations=observations,
            socket_type_detected=final_type,
            detection_method="HYBRID",
        )

    @staticmethod
    def _bbox_overlap(a: list[float], b: list[float], pad: float = 0.5) -> bool:
        """True si el centro de una caja cae dentro de la otra (ampliada
        `pad` de su propio tamano): se consideran el mismo pin."""

        def expand(box: list[float]) -> list[float]:
            w = max(1e-6, box[2] - box[0])
            h = max(1e-6, box[3] - box[1])
            return [box[0] - pad * w, box[1] - pad * h, box[2] + pad * w, box[3] + pad * h]

        ea, eb = expand(a), expand(b)
        ca = ((a[0] + a[2]) / 2.0, (a[1] + a[3]) / 2.0)
        cb = ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)
        return (ea[0] <= cb[0] <= ea[2] and ea[1] <= cb[1] <= ea[3]) or (
            eb[0] <= ca[0] <= eb[2] and eb[1] <= ca[1] <= eb[3]
        )

    def _parse_socket_bbox(self, content: str) -> Optional[list[float]]:
        """Extrae socket_bbox de la respuesta de localizacion; None si es
        invalida o equivale a la imagen completa (fallback sin crop)."""
        json_match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        else:
            start = content.find("{")
            end = content.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None
            content = content[start : end + 1]
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return None
        raw = data.get("socket_bbox") if isinstance(data, dict) else None
        bbox = self._normalize_bbox(raw)
        if bbox is None:
            return None
        # Caja que abarca casi toda la imagen = "no hay socket": usar full
        if (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) > 0.95:
            return None
        return bbox

    def _parse_response(self, content: str) -> AIResult:
        json_match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        else:
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1:
                content = content[start : end + 1]

        data = json.loads(content)
        data["defects"] = self._normalize_defects(data.get("defects"))
        # Compatibilidad: si el modelo no dio listas viejas, las derivamos de defects
        if not data.get("defect_locations") and data["defects"]:
            data["defect_locations"] = [
                d.description for d in data["defects"] if d.description
            ]
        if not data.get("defect_count"):
            data["defect_count"] = len(data["defects"])
        return AIResult(**data)

    @staticmethod
    def _normalize_defects(raw) -> list[DefectInfo]:
        """Tolera 'defects' ausente, como texto suelto o con bbox invalida."""
        if not isinstance(raw, list):
            return []
        out: list[DefectInfo] = []
        for item in raw:
            if isinstance(item, str):
                out.append(DefectInfo(description=item))
            elif isinstance(item, dict):
                out.append(
                    DefectInfo(
                        description=str(
                            item.get("description")
                            or item.get("location")
                            or item.get("descripcion")
                            or ""
                        ),
                        bbox=AIProxyService._normalize_bbox(item.get("bbox")),
                    )
                )
        return out

    @staticmethod
    def _normalize_bbox(raw) -> Optional[list[float]]:
        """Valida bbox [x1,y1,x2,y2] en fracciones 0.0-1.0; None si es invalida."""
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            return None
        try:
            nums = [float(v) for v in raw]
        except (TypeError, ValueError):
            return None
        # Rechaza coordenadas en pixeles (valor demasiado grande para fraccion)
        if any(v < -0.25 or v > 1.25 for v in nums):
            return None
        nums = [min(1.0, max(0.0, v)) for v in nums]
        x1, y1, x2, y2 = nums
        x1, x2 = min(x1, x2), max(x1, x2)
        y1, y2 = min(y1, y2), max(y1, y2)
        if x2 - x1 < 0.002 or y2 - y1 < 0.002:
            return None
        return [x1, y1, x2, y2]


# Singleton global
ai_proxy_service = AIProxyService()
