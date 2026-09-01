"""Tests del cliente IA (ai_proxy) con transporte mock de httpx."""
from __future__ import annotations

import json

import httpx
import pytest

from app.config import settings
from app.services.ai_proxy import AIProxyService, AIResult, ai_proxy_service


def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=httpx.Request("POST", "http://ai/chat/completions"),
    )


def _models_response(ids: list[str]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"object": "list", "data": [{"id": i} for i in ids]},
        request=httpx.Request("GET", "http://ai/models"),
    )


# Cliente real, capturado una sola vez (antes de cualquier patch) para
# permitir re-patchear el transporte dentro del mismo test.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _patch_transport(monkeypatch, handler):
    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


PASS_JSON = {
    "status": "PASS",
    "defect_count": 0,
    "defect_locations": [],
    "confidence": 0.97,
    "observations": "Todo en orden",
    "socket_type_detected": "INTEL_LGA1700",
}


async def test_analyze_socket_parses_fenced_json(monkeypatch):
    content = "Aquí va el analisis:\n```json\n" + json.dumps(PASS_JSON) + "\n```"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        assert body["model"] == settings.ai_model_name
        assert body["messages"][1]["content"][1]["type"] == "image_url"
        return _chat_response(content)

    _patch_transport(monkeypatch, handler)
    service = AIProxyService()

    result = await service.analyze_socket("aW1hZ2U=", "SER123", "AUTO")

    assert result.status == "PASS"
    assert result.confidence == 0.97
    assert result.defect_count == 0


async def test_analyze_socket_parses_plain_json(monkeypatch):
    content = "Claro, el resultado es: " + json.dumps(PASS_JSON) + " fin"

    _patch_transport(monkeypatch, lambda r: _chat_response(content))
    result = await AIProxyService().analyze_socket("x", "SER1")
    assert result.status == "PASS"
    assert result.socket_type_detected == "INTEL_LGA1700"


async def test_analyze_socket_invalid_json_raises(monkeypatch):
    _patch_transport(monkeypatch, lambda r: _chat_response("esto no es JSON"))
    with pytest.raises((ValueError, json.JSONDecodeError)):
        await AIProxyService().analyze_socket("x", "SER1")


async def test_analyze_socket_failure_opens_circuit_breaker(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("AI down")

    _patch_transport(monkeypatch, handler)
    monkeypatch.setattr(settings, "ai_max_retries", 0)
    service = AIProxyService()

    with pytest.raises(httpx.ConnectError):
        await service.analyze_socket("x", "SER1")

    assert service.circuit_open is True

    with pytest.raises(RuntimeError, match="Circuit breaker OPEN"):
        await service.analyze_socket("x", "SER1")


async def test_analyze_socket_success_closes_circuit(monkeypatch):
    monkeypatch.setattr(settings, "ai_max_retries", 0)
    service = AIProxyService()
    service.circuit_open = False

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("AI down")

    _patch_transport(monkeypatch, handler)
    with pytest.raises(httpx.ConnectError):
        await service.analyze_socket("x", "SER1")
    assert service.circuit_open is True

    # half-open manual: el proxy rechaza inmediato con el circuito abierto
    service.circuit_open = False
    _patch_transport(monkeypatch, lambda r: _chat_response(json.dumps(PASS_JSON)))
    result = await service.analyze_socket("x", "SER1")
    assert result.status == "PASS"
    assert service.circuit_open is False


async def test_list_models(monkeypatch):
    _patch_transport(
        monkeypatch, lambda r: _models_response(["qwen3-vl", "llama3"])
    )
    models = await AIProxyService().list_models()
    assert [m["id"] for m in models] == ["qwen3-vl", "llama3"]


async def test_check_connection_ok(monkeypatch):
    _patch_transport(monkeypatch, lambda r: _models_response(["qwen3-vl"]))
    ok, detail, names = await ai_proxy_service.check_connection()
    assert ok is True
    assert names == ["qwen3-vl"]


async def test_check_connection_failure(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"error": "boom"},
            request=httpx.Request("GET", "http://ai/models"),
        )

    _patch_transport(monkeypatch, handler)
    ok, detail, names = await ai_proxy_service.check_connection()
    assert ok is False
    assert names == []


def test_parse_response_invalid_status_rejected():
    bad = {**PASS_JSON, "status": "MAYBE"}
    with pytest.raises(ValueError):
        AIResult(**bad)
