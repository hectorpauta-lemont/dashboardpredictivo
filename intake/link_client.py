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


def extract_survey_partner_id(survey_supplier_detail: dict) -> str | None:
    """Extrae el id de survey_partner desde un detalle de /surveysupplier/{id}/,
    para pasarselo a get_survey_partner_detail() aparte. Compartido entre
    run_allocator (encuestas AWARDED) y run_earnings_tracker (encuestas LIVE)
    -- ver docstring de get_survey_partner_detail para el porque.

    Dos formas de link distintas segun como este armado el precio (confirmado
    2026-09-21, `type.name` en survey_partner_survey_supplier):
      - SURVEY_PARTNER_SURVEY_SUPPLIER: precio parejo para toda la encuesta,
        el id esta directo en survey_partner.id.
      - SURVEY_PARTNER_QUOTA_SURVEY_SUPPLIER: precio por quota (segmento
        demografico) -- survey_partner queda en null, el id real esta un
        nivel mas adentro, en survey_partner_quota.survey_partner.id. Este
        caso es justo el que trae cost_per_interview distinto por quota.

    None si ninguna de las dos formas trae dato -- nunca se adivina."""
    links = survey_supplier_detail.get("survey_partner_survey_supplier") or []
    if not links:
        return None
    link = links[0]
    direct = (link.get("survey_partner") or {}).get("id")
    if direct:
        return direct
    return ((link.get("survey_partner_quota") or {}).get("survey_partner") or {}).get("id") or None


def extract_survey_partner_quota_id(survey_supplier_detail: dict) -> int | None:
    """Cuando el precio es por quota (ver extract_survey_partner_id), este es
    el id de la quota EXACTA que le corresponde a este survey_supplier -- deja
    ubicar su cost_per_interview preciso dentro de
    raw_json_api.quotas[].quota_id, en vez de promediar entre todas las
    quotas de la encuesta. None si el precio es parejo (sin quota puntual) o
    si no hay link."""
    links = survey_supplier_detail.get("survey_partner_survey_supplier") or []
    if not links:
        return None
    return (links[0].get("survey_partner_quota") or {}).get("id") or None


def get_survey_partner_detail(survey_partner_id: str, jwt_token: str) -> dict:
    """
    GET /api/v1/surveypartner/{id}/ -- requiere JWT (LINK_JWT_TOKEN), el token
    de servicio da 401 (confirmado 2026-09-21, mismo patron que
    get_allocations_for_survey). El campo `raw_json_api` de la respuesta trae
    el desglose por QUOTA de la encuesta (segmento demografico): cada quota
    tiene su propio `cost_per_interview` -- lo que se le cobra al CLIENTE por
    esa quota puntual, no lo que se le paga al proveedor (eso sigue viniendo
    de surveysupplier/raw_allocations) -- y sus propias metricas de duracion
    (`length_of_interview`, `median_length_of_interview`).

    El `survey_partner_id` para una encuesta dada sale de
    AwardedSurvey.raw_survey_supplier["survey_partner_survey_supplier"][0]
    ["survey_partner"]["id"] -- confirmado poblado en ~86% de las encuestas
    (95/111 el 2026-09-21). Puede fallar con 401 si el JWT esta vacio/vencido:
    el llamador debe capturar LinkInternalApiError y seguir sin este dato,
    igual que get_allocations_for_survey -- nunca abortar la evaluacion.
    """
    if not jwt_token:
        raise LinkInternalApiError(401, "LINK_JWT_TOKEN no configurado")
    url = f"{settings.LINK_API_BASE_URL}/surveypartner/{survey_partner_id}/"
    response = requests.get(url, headers=_jwt_headers(jwt_token), timeout=15)
    if not response.ok:
        raise LinkInternalApiError(response.status_code, response.text)
    data = response.json()
    if not data.get("success"):
        raise LinkInternalApiError(200, str(data.get("errors")))
    return data["data"]
