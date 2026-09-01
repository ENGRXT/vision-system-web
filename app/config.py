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

DEFAULT_SYSTEM_PROMPT = """You are an expert computer vision quality inspector specialized in CPU socket pins.
You analyze images of motherboard CPU sockets (Intel LGA and AMD PGA/AM5 types).
Your task is to detect bent, broken, missing, or damaged pins.

Rules:
1. Respond ONLY with a valid JSON object. No markdown, no explanations outside JSON.
2. Be extremely precise. A pin is bent if it deviates significantly from the vertical/expected angle.
3. If no defects are found, status must be PASS and defect_count must be 0.
4. If any defect is found, status must be FAIL and describe each location.
5. Confidence must be a float between 0.0 and 1.0.
6. The socket_type_detected field should be INTEL_LGA or AMD_AM5 based on visual cues.

JSON Schema:
{
  "status": "string (PASS|FAIL|UNCERTAIN)",
  "defect_count": "integer",
  "defect_locations": ["string"],
  "confidence": "float",
  "observations": "string",
  "socket_type_detected": "string"
}"""

USER_PROMPT_TEMPLATE = (
    "Analyze the following CPU socket image. The motherboard serial number is {serial}. "
    "Socket type hint: {socket_type_hint} (if known, otherwise auto-detect). "
    "Provide your analysis in the required JSON format."
)


class AIConfig(BaseModel):
    """Configuracion del servicio de IA (vLLM / Qwen3-VL)."""

    base_url: str = "http://10.145.123.27:8000/v1"
    model_name: str = "qwen3-vl"
    api_key: str = "dummy"
    timeout_seconds: float = 180.0
    max_retries: int = 2
    max_tokens: int = 4096
    temperature: float = 0.0
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
        "postgresql+asyncpg://vision:vision_password_change_me@postgres:5432/vision_system",
    )
    log_level: str = _y("log_level", "INFO")
    cors_origins: list[str] = _y("cors_origins", ["*"])

    ai_base_url: str = _y("ai_base_url", "http://10.145.123.27:8000/v1")
    ai_model_name: str = _y("ai_model_name", "qwen3-vl")
    ai_api_key: str = _y("ai_api_key", "dummy")
    ai_timeout_seconds: float = _y("ai_timeout_seconds", 180.0)
    ai_max_retries: int = _y("ai_max_retries", 2)
    ai_max_tokens: int = _y("ai_max_tokens", 4096)
    ai_temperature: float = _y("ai_temperature", 0.0)

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
        )

    @property
    def storage(self) -> StorageConfig:
        return StorageConfig(base_path=self.storage_base_path)


settings = Settings()
