"""
Cliente para la API interna de Link (api.thinknow.link) -- mismo patron que
screening/link_client.py en eros-prime-gate (funciones sueltas, no clase,
mismo LinkInternalApiError, mismo sobre {success, data, errors}). Reutiliza el
mismo token de servicio que ya tiene configurado eros-prime-gate.

Endpoints confirmados contra la API real el 2026-08-17 (no adivinados):

GET /api/v1/surveysupplier/?status=1      -- lista de encuestas AWARDED
    AWARDED_STATUS_ID=1 confirmado empiricamente (habia 18 en ese momento).
    Ojo: la lista NO trae cpi/cpi_client/type_industry -- solo el detalle.
GET /api/v1/surveysupplier/{id}/          -- detalle completo: cpi, cpi_client,
    profit_percentage (campo de Link, default 50 -- no es el piso de negocio,
    ver rules_engine/rules_config.py), project.client.name (buyer),
    type_industry.name, ir, loi, noc, status.
GET /api/v1/surveysupplier-allocations-optimized/ -- confirmado el 2026-08-26
    que devuelve 401 con el token de servicio ('Authorization: Token <token>',
    el que usa /surveysupplier/), pero SI funciona con 'Authorization: Bearer
    <jwt>'. Usa LINK_JWT_TOKEN (settings), un JWT personal temporal (vence
    2026-09-02, ver .env) -- no un token de servicio permanente. Si esta
    vacio/vencido, orchestrator debe tratar el fallo como "sin datos de
    allocations", nunca como error fatal -- ver
    orchestrator/management/commands/run_allocator.py. Cada resultado trae
    client_supplier__name, active, is_blocked y statistics (prescreens,
    completes, conversion_rate, avg_cpi, entrants) -- esto es lo que arma el
    ranking de "mejores suppliers".
GET /api/v1/surveysupplier/?status=2       -- lista de encuestas LIVE.
    LIVE_STATUS_ID=2 (mismo valor confirmado en eros-prime-gate). A
    diferencia de AWARDED, esta lista SI trae progreso en vivo
    (qty_progress_completes, qty_progress_quota, avg_cpi) y project_client_name
    -- pero no cpi/cpi_client exactos, esos siguen necesitando el detalle.
"""

import requests
from django.conf import settings

AWARDED_STATUS_ID = 1
LIVE_STATUS_ID = 2


class LinkInternalApiError(Exception):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Link internal API respondio {status_code}: {body[:300]}")


def _headers(token: str) -> dict:
    return {"Authorization": f"Token {token}"}


def _jwt_headers(jwt_token: str) -> dict:
    return {"Authorization": f"Bearer {jwt_token}"}


def _get_survey_suppliers_by_status(token: str, status_id: int, max_results: int) -> list[dict]:
    """
    Pagina de verdad -- confirmado el 2026-08-18 que el servidor recorta el
    tamano de pagina a 50 sin importar el 'limit' pedido (probado con
    limit=200, volvieron 50). Sin este loop, cualquier estado con mas de 50
    filas (ej. LIVE, con 145 activas) se ve incompleto en silencio.
    """
    url = f"{settings.LINK_API_BASE_URL}/surveysupplier/"
    results: list[dict] = []
    offset = 0
    page_size = 50
    while len(results) < max_results:
        params = {
            "offset": offset,
            "limit": min(page_size, max_results - len(results)),
            "ordering": "-date_created",
            "status": status_id,
        }
        response = requests.get(url, headers=_headers(token), params=params, timeout=20)
        if not response.ok:
            raise LinkInternalApiError(response.status_code, response.text)
        data = response.json()
        if not data.get("success"):
            raise LinkInternalApiError(200, str(data.get("errors")))
        page = data["data"]["results"]
        results.extend(page)
        if len(page) < page_size:
            break  # ultima pagina
        offset += len(page)
    return results


def get_awarded_survey_suppliers(token: str, limit: int = 200) -> list[dict]:
    """
    Lista todas las encuestas en estado AWARDED (pagina hasta `limit`). La
    lista no trae CPI ni type_industry -- hay que pedir el detalle por cada
    una (get_survey_supplier_detail) para evaluarlas con rules_engine.
    """
    return _get_survey_suppliers_by_status(token, AWARDED_STATUS_ID, limit)


def get_survey_supplier_detail(survey_supplier_id: str, token: str) -> dict:
    url = f"{settings.LINK_API_BASE_URL}/surveysupplier/{survey_supplier_id}/"
    response = requests.get(url, headers=_headers(token), timeout=15)
    if not response.ok:
        raise LinkInternalApiError(response.status_code, response.text)
    data = response.json()
    if not data.get("success"):
        raise LinkInternalApiError(200, str(data.get("errors")))
    return data["data"]


def get_live_survey_suppliers(token: str, limit: int = 200) -> list[dict]:
    """
    Lista TODAS las encuestas LIVE (pagina hasta `limit`, default 200 --
    cubre el total real visto el 2026-08-18, ~145). Trae project_client_name
    (buyer) y progreso (qty_progress_completes/quota) sin llamada adicional --
    solo cpi/cpi_client exactos requieren get_survey_supplier_detail aparte.
    """
    return _get_survey_suppliers_by_status(token, LIVE_STATUS_ID, limit)


def get_allocations_for_survey(survey_supplier_id: str, jwt_token: str) -> list[dict]:
    """
    Requiere JWT (LINK_JWT_TOKEN), no el token de servicio -- ver nota del
    modulo. Puede fallar con 401 si el JWT esta vacio/vencido: el llamador
    debe capturar LinkInternalApiError y seguir con una lista vacia, no
    abortar la evaluacion de la encuesta.
    """
    if not jwt_token:
        raise LinkInternalApiError(401, "LINK_JWT_TOKEN no configurado")
    url = f"{settings.LINK_API_BASE_URL}/surveysupplier-allocations-optimized/"
    params = {"survey_supplier": survey_supplier_id}
    response = requests.get(url, headers=_jwt_headers(jwt_token), params=params, timeout=15)
    if not response.ok:
        raise LinkInternalApiError(response.status_code, response.text)
    data = response.json()
    if not data.get("success"):
        raise LinkInternalApiError(200, str(data.get("errors")))
    results = data["data"]
    return results.get("results", results) if isinstance(results, dict) else results
