"""Configuracion tipada de VISION-SYSTEM WEB.

Prioridad de valores: variables de entorno (.env) > config.yaml (VISION_CONFIG) > defaults.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCATE_SOCKET_PROMPT = """You are an expert at locating CPU sockets on motherboard photos.
Find the CPU socket: the rectangular area with the dense grid of metal pins, usually
surrounded by metal retention brackets (Intel LGA) or a single retention bracket and
locking lever (AMD). If the image contains several similar rectangular objects, choose
the one with the dense pin grid (the CPU socket), NOT capacitors, heatsinks, RAM slots
or other chips.

Respond ONLY with a valid JSON object, no markdown, no extra text:
{"socket_bbox": [x1, y1, x2, y2]}

The values are FRACTIONS between 0.0 and 1.0 of the image width/height (origin at the
top-left corner, x1 < x2, y1 < y2). NEVER use pixel values: for a socket at the
image center, a correct answer looks like [0.3, 0.3, 0.7, 0.7], NOT [300, 300, 700, 700].
The box must tightly enclose the ENTIRE pin field (all the pins) with a small margin.

If no CPU socket is visible in the image, respond exactly:
{"socket_bbox": [0, 0, 1, 1]}"""

DEFAULT_SYSTEM_PROMPT = """You are an expert computer vision quality inspector specialized in CPU socket pins.
You analyze images of motherboard CPU sockets (Intel LGA and AMD PGA/AM5 types).
Your task is to detect bent, broken, missing, or damaged pins.

CONTEXT: the image you receive is a CROPPED AND ENLARGED view of the CPU socket pin
field. Everything visible in the frame belongs to the socket area; there are no other
board components in the frame. Evaluate ONLY the socket pins.

Rules:
1. Respond ONLY with a valid JSON object. No markdown, no explanations outside JSON.
2. Be extremely precise. A pin is bent if it deviates from the vertical/expected angle,
   EVEN SLIGHTLY: report the minimum visible deviation (slight curvature, tilt,
   discoloration/oxidation, deformation).
3. If no defects are found, status must be PASS and defect_count must be 0.
4. If any defect is found, status must be FAIL and describe each defect.
5. Confidence must be a float between 0.0 and 1.0.
6. The socket_type_detected field must be one of: INTEL_LGA1151, INTEL_LGA1200,
   INTEL_LGA1700, AMD_AM4, AMD_AM5 (or UNKNOWN if not determinable). Do NOT default to INTEL_LGA1700 for every
   Intel socket. Identification cues:
   - Your estimated pin count (rule 8) is the strongest signal: pick the type whose reference
     count is closest to it.
   - LGA1151/LGA1200: smaller, denser pin field (~1200 pins) than LGA1700 (~1700 pins).
   - LGA1700 vs AMD AM5 (similar pin counts): distinguish by the retention hardware -
     Intel LGA uses TWO metal retention brackets (top and bottom); AMD AM5 has a SINGLE
     overhanging bracket on one side.
   - If the operator confirmed a socket type, report it as socket_type_detected unless
     the image shows clear visual evidence contradicting it.
7. ALL text values in the JSON (observations, defects descriptions) MUST be written in Spanish (español).
   Status, socket_type_detected and enum-like values stay in English as defined in the schema.
8. pin_count: count the total number of pins visible in this enlarged pin field (best estimate
   if exact counting is impossible). Reference values: INTEL_LGA1151 = 1151, INTEL_LGA1200 = 1200,
   INTEL_LGA1700 = 1700, AMD_AM4 = 1331, AMD_AM5 = 1718. NEVER assume a reference value without
   counting; do not count a single row and extrapolate; if your count is far from every
   reference, report the closest type and mention the difference in observations.
9. FOCUS RULE: examine the pin field pin by pin with maximum attention. A SINGLE bent, broken or
   missing pin among hundreds of healthy pins MUST be detected, even if the deviation is small.
   Do not miss isolated defects.
10. For every defective pin, add an entry to "defects" with:
    - "description": Spanish description of the defect (e.g. "Pin doblado hacia afuera", "Pin faltante").
    - "bbox": [x1, y1, x2, y2] bounding box of that specific pin, as FRACTIONS between 0.0 and 1.0 of
      the image width/height, origin at the top-left corner (x1 < x2, y1 < y2). The box must tightly
      surround the defective pin only and stay INSIDE the pin field. If you cannot localize it,
      omit "bbox" (do not invent coordinates).

JSON Schema:
{
  "status": "string (PASS|FAIL|UNCERTAIN)",
  "defect_count": "integer",
  "pin_count": "integer",
  "defects": [
    {"description": "string (español)", "bbox": [x1, y1, x2, y2]}
  ],
  "confidence": "float",
  "observations": "string (descripción en español)",
  "socket_type_detected": "string (INTEL_LGA1151|INTEL_LGA1200|INTEL_LGA1700|AMD_AM4|AMD_AM5|UNKNOWN)"
}"""

# System prompt EXCLUSIVO de la etapa de verificacion hibrida: el prompt
# por defecto (DEFAULT_SYSTEM_PROMPT) ordena "add an entry to defects" por
# CADA pin defectuoso, lo que contradice la regla de no repetir los defectos
# ya medidos y hace que el VLM los vuelva a listar (~50 tokens de mas).
# Este prompt compacto alinea el esquema y acorta la respuesta: el servidor
# vLLM genera ~7 tok/s, asi que cada token ahorrado es tiempo real.
VERIFY_SYSTEM_PROMPT = """You are an expert CPU socket pin quality inspector VERIFYING a
deterministic computer-vision measurement. The measurement (pin count, socket
type, located defects) is authoritative: do not re-count, do not re-type.
The image is a cropped, enlarged view of the socket pin field only.
Your job: visually confirm the measurement and find ADDITIONAL defective pins
(bent, broken, missing or damaged) that are NOT in the measured list.

Respond ONLY with a valid, COMPACT JSON object with EXACTLY these fields:
{
  "status": "PASS|FAIL",
  "defect_count": <integer: measured + additional>,
  "pin_count": <keep the measured value>,
  "defects": [ {"description": "español, max 6 words", "bbox": [x1, y1, x2, y2]} ],
  "confidence": <float 0.0-1.0>,
  "observations": "español, max 25 words; use empty string if nothing to add",
  "socket_type_detected": "<keep the measured value>"
}
- "defects" lists ONLY ADDITIONAL defective pins; NEVER repeat or restate the
  measured ones.
- "bbox": tight box around that specific pin, as fractions 0.0-1.0 of this
  image (origin top-left). If you cannot localize a defect, omit that defect.
- "status" = FAIL if any defect exists (measured or additional), PASS otherwise.
- No markdown, no prose outside JSON, no repeated sentences. Keep the JSON as
  short as possible: a long answer blocks the production line.
"""


# Prompt de usuario cuando el operador NO indico tipo de socket (AUTO):
USER_PROMPT_TEMPLATE = (
    "Analyze this CPU socket pin field image (it is already cropped to the socket and enlarged). "
    "The motherboard serial number is {serial}. The operator did not specify a socket type: "
    "identify it yourself using the reference pin counts and the visual cues. "
    "Count the pins carefully and inspect every pin for bent, missing or damaged condition, "
    "even the smallest deviation. "
    "Provide your analysis in the required JSON format."
)

# Prompt de usuario cuando el operador CONFIRMO el tipo de socket:
USER_PROMPT_CONFIRMED_TEMPLATE = (
    "Analyze this CPU socket pin field image (it is already cropped to the socket and enlarged). "
    "The motherboard serial number is {serial}. The operator CONFIRMED the socket type is "
    "{socket_hint}: report socket_type_detected as {socket_hint} unless the image shows clear "
    "visual evidence of a different type. Your main task: count the pins carefully and detect "
    "any bent, missing or damaged pin, even the most subtle one. "
    "Provide your analysis in the required JSON format."
)


# Prompt de VERIFICACION: la medicion CV (OpenCV) ya conto los pines e
# identifico el socket de forma determinista (exacta, no estimada). El VLM solo
# confirma visualmente y anade defectos que vea con claridad que la medicion no
# listara. NUNCA modifica el conteo ni el tipo medidos: contandolos, el VLM
# "estima" (decia 1700 en una placa de 1151) y puede alucinar.
USER_PROMPT_VERIFY_TEMPLATE = (
    "You are verifying a MEASURED inspection of this CPU socket pin field (image "
    "already cropped and enlarged). The motherboard serial number is {serial}. "
    "A deterministic computer-vision measurement (exact, not estimated) produced:\n"
    "- Pin count (measured): {pin_count}\n"
    "- Socket type (measured): {socket_type}\n"
    "- Defective pins already located by the measurement:\n{cv_defects}\n\n"
    "STRICT RULES:\n"
    "1. KEEP the measured values: report pin_count = {pin_count} and "
    "socket_type_detected = {socket_type} EXACTLY as given. Do not re-count and do "
    "not change them.\n"
    "2. Examine the pin field visually. Report ONLY additional defective pins that "
    "you clearly see and that are NOT in the list above (bent, broken, missing or "
    "damaged). If you are not fully certain a pin is defective, do NOT report it.\n"
    "3. If the image clearly contradicts the measured socket type, keep the measured "
    "type in socket_type_detected but mention it in ONE short phrase.\n"
    "4. status = FAIL if any defect exists (measured or verified by you), PASS otherwise.\n"
    "5. For each ADDITIONAL defect you report, include its tight bbox as fractions of "
    "the image. Do not repeat the already-measured defects.\n"
    "SPEED RULE: your JSON is parsed automatically and the inference server is slow, "
    "so a long answer blocks the production line. Respond with the SHORTEST valid "
    "JSON: use EXACTLY the fields status, defect_count, pin_count, defects, "
    "confidence, observations, socket_type_detected. Each item of \"defects\" must "
    "use exactly {{\"description\": \"...\", \"bbox\": [x1,y1,x2,y2]}}. "
    "Descriptions in Spanish with MAX 6 words each. \"observations\" in Spanish with "
    "MAX 25 words (use \"\" if nothing to add). No markdown, no prose outside JSON, "
    "no restating the measured list, no repeated sentences."
    "Respond ONLY with the required JSON object."
)


class AIConfig(BaseModel):
    """Configuracion del servicio de IA (vLLM / Qwen3-VL)."""

    base_url: str = "http://localhost:8000/v1"
    model_name: str = "qwen3-vl"
    api_key: str = "dummy"
    # Timeout acotado: en produccion una llamada no debe bloquear mas de 60 s
    # (con medicion CV, al agotarlo se degrada a resultado solo-CV).
    timeout_seconds: float = 60.0
    max_retries: int = 2
    # La respuesta es un JSON compacto (~150 tokens); el tope bajo limita
    # generaciones interminables del modelo (antes 4096).
    max_tokens: int = 512
    temperature: float = 0.0
    # Modo "thinking" de Qwen3: OFF. Con thinking, el modelo genera miles de
    # tokens de razonamiento antes del JSON (~90 s por llamada); sin el,
    # responde directo (~10-20 s).
    enable_thinking: bool = False
    system_prompt: str = DEFAULT_SYSTEM_PROMPT


class StorageConfig(BaseModel):
    """Configuracion del almacenamiento de evidencia."""

    base_path: str = "/app/evidence"


def _load_yaml_config() -> dict[str, Any]:
    path = os.environ.get("VISION_CONFIG", "")
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_yaml_defaults = _load_yaml_config()


def _y(key: str, default: Any) -> Any:
    return _yaml_defaults.get(key, default)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = _y("app_name", "VISION-SYSTEM WEB")
    app_version: str = _y("app_version", "4.0.0")
    database_url: str = _y(
        "database_url",
        "postgresql+asyncpg://vision:CHANGE_ME_TO_A_UNIQUE_RANDOM_VALUE@postgres:5432/vision_system",
    )
    log_level: str = _y("log_level", "INFO")
    cors_origins: list[str] = _y("cors_origins", ["*"])

    ai_base_url: str = _y("ai_base_url", "http://localhost:8000/v1")
    ai_model_name: str = _y("ai_model_name", "qwen3-vl")
    ai_api_key: str = _y("ai_api_key", "dummy")
    ai_timeout_seconds: float = _y("ai_timeout_seconds", 60.0)
    ai_max_retries: int = _y("ai_max_retries", 2)
    ai_max_tokens: int = _y("ai_max_tokens", 512)
    ai_temperature: float = _y("ai_temperature", 0.0)
    # Thinking de Qwen3: off por velocidad (ver AIConfig.enable_thinking)
    ai_enable_thinking: bool = _y("ai_enable_thinking", False)

    storage_base_path: str = _y("storage_base_path", "/app/evidence")

    @property
    def ai(self) -> AIConfig:
        return AIConfig(
            base_url=self.ai_base_url,
            model_name=self.ai_model_name,
            api_key=self.ai_api_key,
            timeout_seconds=self.ai_timeout_seconds,
            max_retries=self.ai_max_retries,
            max_tokens=self.ai_max_tokens,
            temperature=self.ai_temperature,
            enable_thinking=self.ai_enable_thinking,
        )

    @property
    def storage(self) -> StorageConfig:
        return StorageConfig(base_path=self.storage_base_path)


settings = Settings()
