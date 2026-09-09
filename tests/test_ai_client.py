"""Tests del cliente IA (ai_proxy) con transporte mock de httpx.

Pipeline hibrido:
  - Con medicion CV: UNA sola llamada al VLM (verificacion de la medicion).
  - Sin medicion CV (imagen sin socket medible): camino clasico de 2 llamadas:
    1) localizar el socket (devuelve socket_bbox)
    2) analizar el crop (devuelve el JSON de resultado)
El handler mock distingue las llamadas por orden de llegada.
"""
from __future__ import annotations

import base64
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.config import settings
from app.services.ai_proxy import AIProxyService, AIResult, DefectInfo, ai_proxy_service
from app.utils import socket_cv


def _png_bytes() -> bytes:
    from tests.conftest import png_bytes

    return png_bytes()


def _grid_png(rows: int, cols: int, **kwargs) -> bytes:
    """PNG de una reticula sintetica de pines (ver make_grid en
    tests.test_socket_cv): el CV SI localiza y mide el socket."""
    from tests.test_socket_cv import make_grid

    buf = BytesIO()
    make_grid(rows, cols, **kwargs).save(buf, format="PNG")
    return buf.getvalue()


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
    "pin_count": 1151,
}

LOCATE_JSON = {"socket_bbox": [0.2, 0.25, 0.7, 0.75]}


def _two_stage_handler(locate_content=None, analyze_content=None):
    """Handler que responde la llamada 1 con localizacion y la 2 con analisis."""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return _chat_response(
                locate_content if locate_content is not None else json.dumps(LOCATE_JSON)
            )
        return _chat_response(
            analyze_content
            if analyze_content is not None
            else json.dumps(PASS_JSON)
        )

    return handler, state


def _single_call_handler(content: str):
    """Camino hibrido: la UNICA llamada VLM es la verificacion (el CV ya
    localizo y midio), asi que no hay etapa de localizacion."""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return _chat_response(content)

    return handler, state


async def test_analyze_socket_two_stage_parses_fenced_json(monkeypatch):
    content = "Aquí va el analisis:\n```json\n" + json.dumps(PASS_JSON) + "\n```"
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        assert body["model"] == settings.ai_model_name
        assert body["messages"][1]["content"][1]["type"] == "image_url"
        state["n"] += 1
        if state["n"] == 1:
            return _chat_response(json.dumps(LOCATE_JSON))
        return _chat_response(content)

    _patch_transport(monkeypatch, handler)
    result = await AIProxyService().analyze_socket(_png_bytes(), "SER123", "AUTO")

    # 2 llamadas al VLM: localizacion + analisis del crop
    assert state["n"] == 2
    assert result.status == "PASS"
    assert result.confidence == 0.97
    assert result.defect_count == 0
    assert result.socket_bbox == [0.2, 0.25, 0.7, 0.75]
    assert result.crop_base64 is not None


async def test_analyze_socket_parses_plain_json(monkeypatch):
    content = "Claro, el resultado es: " + json.dumps(PASS_JSON) + " fin"
    handler, _ = _two_stage_handler(analyze_content=content)
    _patch_transport(monkeypatch, handler)
    result = await AIProxyService().analyze_socket(_png_bytes(), "SER1")
    assert result.status == "PASS"
    assert result.socket_type_detected == "INTEL_LGA1700"


async def test_analyze_socket_invalid_json_raises(monkeypatch):
    handler, _ = _two_stage_handler(analyze_content="esto no es JSON")
    _patch_transport(monkeypatch, handler)
    with pytest.raises((ValueError, json.JSONDecodeError)):
        await AIProxyService().analyze_socket(_png_bytes(), "SER1")


async def test_analyze_socket_locate_failure_falls_back_to_full_image(monkeypatch):
    # Localizacion devuelve basura: la inspeccion NO se bloquea, se analiza
    # la imagen completa (socket_bbox None, sin crop)
    handler, state = _two_stage_handler(locate_content="no hay socket visible aqui")
    _patch_transport(monkeypatch, handler)
    result = await AIProxyService().analyze_socket(_png_bytes(), "SER1")

    assert state["n"] == 2
    assert result.status == "PASS"
    assert result.socket_bbox is None
    assert result.crop_base64 is None


async def test_analyze_socket_crop_is_smaller_and_remapped(monkeypatch):
    # Defecto reportado en coordenadas del CROP (centro del crop): al
    # remapear debe caer DENTRO de la region del socket de la imagen completa
    fail_json = {
        "status": "FAIL",
        "defect_count": 1,
        "defects": [
            {"description": "Pin doblado", "bbox": [0.5, 0.5, 0.55, 0.55]}
        ],
        "confidence": 0.9,
        "observations": "Pin doblado detectado",
        "socket_type_detected": "INTEL_LGA1151",
        "pin_count": 1151,
    }
    handler, _ = _two_stage_handler(analyze_content=json.dumps(fail_json))
    _patch_transport(monkeypatch, handler)
    result = await AIProxyService().analyze_socket(_png_bytes(), "SER1", "AUTO")

    assert result.socket_bbox == [0.2, 0.25, 0.7, 0.75]
    assert result.crop_base64 is not None

    # El crop se guarda como PNG valido (puede ser MAYOR que la imagen
    # completa: el pipeline lo amplifica x2 para que el VLM vea detalle)
    crop = Image.open(BytesIO(base64.b64decode(result.crop_base64)))
    assert crop.format == "PNG"
    assert min(crop.size) > 0

    # El centro del defecto (coords del crop remapeadas a la imagen completa)
    # debe caer DENTRO de la region del socket
    d = result.defects[0]
    assert d.bbox is not None
    cx = (d.bbox[0] + d.bbox[2]) / 2
    cy = (d.bbox[1] + d.bbox[3]) / 2
    assert 0.2 <= cx <= 0.7
    assert 0.25 <= cy <= 0.75


async def test_analyze_socket_failure_opens_circuit_breaker(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("AI down")

    _patch_transport(monkeypatch, handler)
    monkeypatch.setattr(settings, "ai_max_retries", 0)
    service = AIProxyService()

    with pytest.raises(httpx.ConnectError):
        await service.analyze_socket(_png_bytes(), "SER1")

    assert service.circuit_open is True

    with pytest.raises(RuntimeError, match="Circuit breaker OPEN"):
        await service.analyze_socket(_png_bytes(), "SER1")


async def test_analyze_socket_success_closes_circuit(monkeypatch):
    monkeypatch.setattr(settings, "ai_max_retries", 0)
    service = AIProxyService()
    service.circuit_open = False

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("AI down")

    _patch_transport(monkeypatch, handler)
    with pytest.raises(httpx.ConnectError):
        await service.analyze_socket(_png_bytes(), "SER1")
    assert service.circuit_open is True

    # half-open manual: el proxy rechaza inmediato con el circuito abierto
    service.circuit_open = False
    handler, _ = _two_stage_handler()
    _patch_transport(monkeypatch, handler)
    result = await service.analyze_socket(_png_bytes(), "SER1")
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


def test_parse_socket_bbox_valid():
    svc = AIProxyService()
    assert svc._parse_socket_bbox('{"socket_bbox": [0.2, 0.25, 0.7, 0.75]}') == [
        0.2,
        0.25,
        0.7,
        0.75,
    ]
    # Con markdown
    fenced = "```json\n" + json.dumps(LOCATE_JSON) + "\n```"
    assert svc._parse_socket_bbox(fenced) == [0.2, 0.25, 0.7, 0.75]


def test_parse_socket_bbox_full_image_is_fallback():
    svc = AIProxyService()
    # Caja que abarca casi toda la imagen (area > 0.95) = "no hay socket"
    # -> None (se analiza la imagen completa)
    assert svc._parse_socket_bbox('{"socket_bbox": [0, 0, 1, 1]}') is None
    assert svc._parse_socket_bbox('{"socket_bbox": [0.01, 0.01, 0.99, 0.99]}') is None
    # Area ligeramente menor: SI es una region valida
    assert svc._parse_socket_bbox(
        '{"socket_bbox": [0.05, 0.05, 0.95, 0.95]}'
    ) == [0.05, 0.05, 0.95, 0.95]


def test_parse_socket_bbox_invalid():
    svc = AIProxyService()
    assert svc._parse_socket_bbox("esto no es json") is None
    assert svc._parse_socket_bbox('{"socket_bbox": "centro"}') is None
    assert svc._parse_socket_bbox('{"otro": 1}') is None
    # Coordenadas en pixeles (valores enormes) se rechazan
    assert svc._parse_socket_bbox('{"socket_bbox": [500, 400, 900, 800]}') is None


# --------------------------------------------------------------------- #
# Pipeline hibrido: medicion CV (autoritativa) + verificacion VLM
# --------------------------------------------------------------------- #


async def test_hybrid_measured_socket_single_vlm_call_cv_authoritative(monkeypatch):
    # El CV localiza y mide (1156 pines = LGA1151); el VLM solo verifica con
    # UNA llamada. Aunque el VLM "estime" otro conteo/tipo, gana la medicion.
    vlm_json = {**PASS_JSON, "pin_count": 1700, "socket_type_detected": "INTEL_LGA1700"}
    state = {"n": 0, "texts": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        body = json.loads(request.content)
        state["texts"].append(body["messages"][1]["content"][0]["text"])
        return _chat_response(json.dumps(vlm_json))

    _patch_transport(monkeypatch, handler)
    result = await AIProxyService().analyze_socket(_grid_png(34, 34), "SER1")

    # 1 llamada total: NO hubo localizacion VLM (la hizo el CV)
    assert state["n"] == 1
    assert "Pin count (measured): 1156" in state["texts"][0]
    assert "Socket type (measured): INTEL_LGA1151" in state["texts"][0]
    assert result.detection_method == "HYBRID"
    assert result.pin_count == 1156
    assert result.socket_type_detected == "INTEL_LGA1151"
    assert result.status == "PASS"
    assert result.socket_bbox is not None
    assert result.crop_base64 is not None
    # Evidencia de la medicion (04_cv_analysis.png): PNG valido
    ev = Image.open(BytesIO(base64.b64decode(result.cv_analysis_base64)))
    assert ev.format == "PNG"


async def test_hybrid_vlm_down_returns_cv_only_result(monkeypatch):
    # IA caida pero el CV ya midio: la inspeccion NO se bloquea, se devuelve
    # el resultado solo CV (detection_method="CV").
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("AI down")

    _patch_transport(monkeypatch, handler)
    monkeypatch.setattr(settings, "ai_max_retries", 0)
    result = await AIProxyService().analyze_socket(_grid_png(34, 34), "SER1")

    assert result.detection_method == "CV"
    assert result.pin_count == 1156
    assert result.socket_type_detected == "INTEL_LGA1151"
    assert result.status == "PASS"
    assert "solo por vision artificial" in result.observations


async def test_hybrid_circuit_open_skips_vlm_and_returns_cv(monkeypatch):
    # Con el circuito abierto (IA caida) y medicion CV: NINGUNA llamada al
    # VLM; se devuelve el resultado solo CV.
    monkeypatch.setattr(settings, "ai_max_retries", 0)
    svc = AIProxyService()

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("AI down")

    _patch_transport(monkeypatch, down)
    # Imagen sin socket: el fallo del VLM SÍ bloquea (no hay medicion CV)
    with pytest.raises(httpx.ConnectError):
        await svc.analyze_socket(_png_bytes(), "SER1")
    assert svc.circuit_open is True

    handler, state = _two_stage_handler()
    _patch_transport(monkeypatch, handler)
    result = await svc.analyze_socket(_grid_png(34, 34), "SER1")
    assert state["n"] == 0
    assert result.detection_method == "CV"
    assert result.pin_count == 1156


async def test_hybrid_missing_pin_fail_and_remap_to_full_image(monkeypatch):
    # Pin faltante (celda interior vacia): el CV lo mide, el VLM verifica,
    # el defecto remapeado (coords del crop -> imagen completa) cae DENTRO
    # de la region del socket localizada.
    vlm_json = {**PASS_JSON, "pin_count": 399, "defects": [], "defect_count": 0,
                "socket_type_detected": "INTEL_LGA1151"}
    handler, state = _single_call_handler(json.dumps(vlm_json))
    _patch_transport(monkeypatch, handler)
    result = await AIProxyService().analyze_socket(
        _grid_png(20, 20, missing={(10, 10)}), "SER1", "INTEL_LGA1151"
    )

    assert state["n"] == 1
    assert result.detection_method == "HYBRID"
    assert result.status == "FAIL"
    assert result.defect_count == 1
    assert result.pin_count == 399
    # Tipo: el CV dijo UNKNOWN (399 no coincide con ninguna referencia) y el
    # operador confirmo INTEL_LGA1151 -> se usa el del operador
    assert result.socket_type_detected == "INTEL_LGA1151"

    d = result.defects[0]
    cx = (d.bbox[0] + d.bbox[2]) / 2
    cy = (d.bbox[1] + d.bbox[3]) / 2
    x1, y1, x2, y2 = result.socket_bbox
    assert x1 <= cx <= x2
    assert y1 <= cy <= y2


async def test_hybrid_verify_prompt_cv_defects_in_crop_coords_and_vlm_remap(monkeypatch):
    # El prompt debe listar los defectos CV en coordenadas del CROP (lo unico
    # que ve el VLM) y el defecto adicional del VLM (coords del crop) debe
    # llegar al resultado final remapeado a la imagen completa.
    from app.utils.image_ops import (
        crop_to_bbox,
        full_to_crop_frac,
        normalize_image,
        remap_bbox_frac,
    )

    vlm_bbox_crop = [0.05, 0.05, 0.09, 0.09]
    vlm_json = {
        **PASS_JSON,
        "status": "FAIL",
        "pin_count": 399,
        "socket_type_detected": "INTEL_LGA1151",
        "defects": [
            {"description": "Pin doblado en la esquina", "bbox": vlm_bbox_crop}
        ],
        "defect_count": 1,
    }
    state = {"n": 0, "texts": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        body = json.loads(request.content)
        state["texts"].append(body["messages"][1]["content"][0]["text"])
        return _chat_response(json.dumps(vlm_json))

    _patch_transport(monkeypatch, handler)
    result = await AIProxyService().analyze_socket(
        _grid_png(20, 20, missing={(10, 10)}), "SER1", "INTEL_LGA1151"
    )

    assert state["n"] == 1
    # Geometria determinista del pipeline (mismas entradas => mismos pixeles)
    norm_img = Image.open(
        BytesIO(normalize_image(_grid_png(20, 20, missing={(10, 10)})))
    )
    det = socket_cv.find_socket(norm_img)
    assert det is not None
    bbox = [round(v, 5) for v in det.bbox_frac]
    crop_box = crop_to_bbox(norm_img, bbox)[1]
    # El crop es una subregion PROPIA (remapear no es la identidad)
    assert crop_box[0] > 0 or crop_box[2] < norm_img.width
    cv = socket_cv.analyze_pins(norm_img, tuple(bbox))
    assert cv is not None and len(cv.defects) == 1
    expected_in_prompt = full_to_crop_frac(
        cv.defects[0].bbox_frac, crop_box, norm_img.size
    )

    # 1) Defectos CV listados en el prompt en coordenadas del CROP
    marker = "Defective pins already located by the measurement:\n"
    prompt = state["texts"][0]
    seg = prompt[prompt.index(marker) + len(marker):].split("\n", 1)[0]
    listed = json.loads(seg)
    assert len(listed) == 1
    for got, exp in zip(listed[0]["bbox"], expected_in_prompt):
        assert abs(got - exp) < 0.01

    # 2) Defecto adicional del VLM en el resultado: coords de imagen COMPLETA
    assert result.defect_count == 2
    exp_full = remap_bbox_frac(vlm_bbox_crop, crop_box, norm_img.size)
    for got, exp in zip(result.defects[1].bbox, exp_full):
        assert abs(got - exp) < 0.01


async def test_hybrid_vlm_confirms_cv_defect_not_duplicated(monkeypatch):
    # El VLM "confirma" el defecto que ya midio el CV (el mismo pin): la
    # fusion NO lo duplica, aunque el VLM lo exprese en coords del crop.
    from app.utils.image_ops import crop_to_bbox, normalize_image

    norm_img = Image.open(
        BytesIO(normalize_image(_grid_png(20, 20, missing={(10, 10)})))
    )
    det = socket_cv.find_socket(norm_img)
    assert det is not None
    bbox = [round(v, 5) for v in det.bbox_frac]
    crop_box = crop_to_bbox(norm_img, bbox)[1]
    cv = socket_cv.analyze_pins(norm_img, tuple(bbox))
    assert cv is not None and len(cv.defects) == 1

    # El MISMO defecto en coordenadas del crop (lo que devolveria el VLM)
    db = cv.defects[0].bbox_frac
    cx = (db[0] + db[2]) / 2 * norm_img.width
    cy = (db[1] + db[3]) / 2 * norm_img.height
    cfx = (cx - crop_box[0]) / (crop_box[2] - crop_box[0])
    cfy = (cy - crop_box[1]) / (crop_box[3] - crop_box[1])
    vlm_json = {
        **PASS_JSON,
        "status": "FAIL",
        "pin_count": 399,
        "socket_type_detected": "INTEL_LGA1151",
        "defects": [
            {
                "description": "Confirma el pin faltante",
                "bbox": [cfx - 0.005, cfy - 0.005, cfx + 0.005, cfy + 0.005],
            }
        ],
        "defect_count": 1,
    }
    handler, state = _single_call_handler(json.dumps(vlm_json))
    _patch_transport(monkeypatch, handler)
    result = await AIProxyService().analyze_socket(
        _grid_png(20, 20, missing={(10, 10)}), "SER1", "INTEL_LGA1151"
    )

    assert state["n"] == 1
    assert result.detection_method == "HYBRID"
    # Un unico defecto (el del CV): el "confirmado" por el VLM se descarta
    assert result.defect_count == 1
    assert "pin faltante" in result.defects[0].description.lower()


async def test_flat_image_still_uses_two_call_vlm_path(monkeypatch):
    # Imagen sin socket medible: camino clasico (localizar + analizar) y
    # detection_method="VLM".
    handler, state = _two_stage_handler()
    _patch_transport(monkeypatch, handler)
    result = await AIProxyService().analyze_socket(_png_bytes(), "SER1")

    assert state["n"] == 2
    assert result.detection_method == "VLM"
    assert result.cv_analysis_base64 is None


def test_merge_cv_vlm_dedups_overlapping_vlm_defect():
    # Fusion: los defectos del CV + los adicionales del VLM, deduplicados por
    # posicion. El VLM "ve" el mismo pin faltante que midio el CV (mismo bbox)
    # -> se descarta el duplicado; un defecto lejano SÍ se anade.
    from tests.test_socket_cv import make_grid

    cv = socket_cv.analyze_pins(make_grid(20, 20, missing={(10, 10)}))
    assert cv is not None and len(cv.defects) == 1

    dup = DefectInfo(
        description="Pin faltante (tambien visto por el VLM)",
        bbox=list(cv.defects[0].bbox_frac),
    )
    extra = DefectInfo(description="Dano extra en la esquina", bbox=[0.02, 0.02, 0.08, 0.08])
    vlm = AIResult(
        status="FAIL",
        defect_count=2,
        defects=[dup, extra],
        confidence=0.9,
        observations="Confirma el pin faltante y ve uno mas",
        socket_type_detected="AMD_AM5",
    )
    result = AIProxyService()._merge_cv_vlm(cv, "INTEL_LGA1151", vlm)

    assert result.detection_method == "HYBRID"
    assert result.defect_count == 2  # 1 CV + 1 VLM unico (el dup se descarta)
    assert result.status == "FAIL"
    assert result.pin_count == cv.pin_count
    # El tipo siempre es el medido/confirmado (CV), no el "estimado" por el VLM
    assert result.socket_type_detected == "INTEL_LGA1151"
    assert "se mantiene el tipo medido por CV" in result.observations
