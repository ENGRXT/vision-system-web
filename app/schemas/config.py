"""Schemas de configuracion del sistema."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ConfigResponse(BaseModel):
    app_name: str
    app_version: str
    ai_base_url: str
    ai_model_name: str
    ai_timeout_seconds: float
    ai_max_retries: int
    storage_base_path: str
    log_level: str
    socket_types: list[str]
    active_ws_connections: int
    database_url: str


class ConfigUpdateRequest(BaseModel):
    ai_base_url: Optional[str] = None
    ai_model_name: Optional[str] = None
    ai_timeout_seconds: Optional[float] = Field(default=None, ge=1, le=600)
    ai_max_retries: Optional[int] = Field(default=None, ge=0, le=5)
    log_level: Optional[str] = Field(default=None, pattern="^(DEBUG|INFO|WARNING|ERROR)$")
    updated_by: Optional[str] = None


class AIConnectionTestResponse(BaseModel):
    ok: bool
    base_url: str
    detail: str
    models: list[str] = Field(default_factory=list)
