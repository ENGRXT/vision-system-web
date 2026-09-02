"""Proxy hacia el modelo VLM local (vLLM, API OpenAI-compatible).

Incluye circuit breaker para manejo de fallos del servicio de IA y
notificacion en tiempo real (AI_SERVICE_DOWN / AI_SERVICE_RECOVERED).
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from app.config import USER_PROMPT_TEMPLATE, settings
from app.schemas.websocket import (
    EVENT_AI_SERVICE_DOWN,
    EVENT_AI_SERVICE_RECOVERED,
)
from app.services.websocket_manager import ws_manager


class AIResult(BaseModel):
    status: str = Field(..., pattern="^(PASS|FAIL|UNCERTAIN)$")
    defect_count: int = Field(..., ge=0)
    defect_locations: list[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    observations: str
    socket_type_detected: str


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

    async def analyze_socket(
        self,
        image_base64: str,
        serial: str,
        socket_hint: str = "",
    ) -> AIResult:
        if self.circuit_open:
            raise RuntimeError("Circuit breaker OPEN: AI service unavailable")

        user_text = USER_PROMPT_TEMPLATE.format(
            serial=serial,
            socket_type_hint=socket_hint or "auto-detect",
        )

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": settings.ai.system_prompt},
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
                                "url": f"data:image/png;base64,{image_base64}"
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
                result = self._parse_response(content)

                if self.circuit_open:
                    self.circuit_open = False
                    self._fail_streak = 0
                    await ws_manager.broadcast(
                        EVENT_AI_SERVICE_RECOVERED,
                        {"timestamp": datetime.now(timezone.utc).isoformat()},
                    )

                self._fail_streak = 0
                return result

            except Exception as exc:
                last_error = exc
                logger.warning(f"AI attempt {attempt + 1} failed: {exc}")
                if attempt < self.max_retries:
                    await asyncio.sleep(2**attempt)

        self.circuit_open = True
        self._fail_streak += 1
        await ws_manager.broadcast(
            EVENT_AI_SERVICE_DOWN,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error_message": str(last_error),
                "retry_count": self.max_retries,
            },
        )
        assert last_error is not None
        raise last_error

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
        return AIResult(**data)


# Singleton global
ai_proxy_service = AIProxyService()
