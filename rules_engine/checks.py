"""
Chequeos deterministicos individuales -- funciones puras, sin DB, sin red, sin
IA. Cada uno responde una sola pregunta de negocio y es trivialmente probable
con fixtures. Ver rules_engine/engine.py para como se combinan.
"""

from dataclasses import dataclass

from rules_engine.rules_config import (
    EXCLUSION_GROUPS,
    IR_ALARM_THRESHOLD_PERCENT,
    PROFIT_FLOOR_PERCENT,
    RANKING_EXCLUDED_TYPE_INDUSTRIES,
    TERMINATION_ALARM_THRESHOLD_PERCENT,
)


def calculate_profit_percent(cpi_client: float, cpi_paid: float) -> float | None:
    """
    Replica exactamente AllocationsListAPIView._calculate_profit en tnr-link:
    round(((cpi_client - cpi_paid) / cpi_client) * 100, 0), sin permitir
    porcentaje negativo. Esa es la vista que se reutiliza para leer el estado
    de CPI/profit, asi que los numeros deben coincidir con lo que ya muestra
    Link -- no con la otra formula que existe en
    apps/router/core/utils.calculate_profit (esa no recorta negativos).

    Devuelve None si cpi_client es 0/None -- la division no esta definida.
    El llamador debe tratar None como "no se puede evaluar", nunca como pass.
    """
    if not cpi_client:
        return None
    raw = ((cpi_client - cpi_paid) / cpi_client) * 100
    return max(round(raw, 0), 0.0)


@dataclass(frozen=True)
class ProfitCheckResult:
    passed: bool
    profit_percent: float | None
    floor_used: float
    reason: str


def apply_profit_floor(
    cpi_client: float, cpi_paid: float, floor_percent: float = PROFIT_FLOOR_PERCENT
) -> ProfitCheckResult:
    profit_percent = calculate_profit_percent(cpi_client, cpi_paid)
    if profit_percent is None:
        return ProfitCheckResult(
            passed=False,
            profit_percent=None,
            floor_used=floor_percent,
            reason="cpi_client_missing_or_zero",
        )
    if profit_percent >= floor_percent:
        return ProfitCheckResult(
            passed=True,
            profit_percent=profit_percent,
            floor_used=floor_percent,
            reason=f"profit {profit_percent}% >= piso {floor_percent}%",
        )
    return ProfitCheckResult(
        passed=False,
        profit_percent=profit_percent,
        floor_used=floor_percent,
        reason=f"profit {profit_percent}% < piso {floor_percent}%",
    )


@dataclass(frozen=True)
class ExclusionCheckResult:
    passed: bool
    triggered_rule_id: str | None
    reason: str


def check_exclusion_conflicts(buyer_name: str, active_supplier_names: list[str]) -> ExclusionCheckResult:
    """
    Chequeo de solo lectura: dentro del conjunto de proveedores YA activos en
    las allocations de esta encuesta, hay un par que se excluye mutuamente
    segun EXCLUSION_GROUPS? No decide si activar un proveedor nuevo seria
    valido -- eso requiere la logica real de SupplierGroup/traffic-rules de
    Link, que hoy no esta expuesta de solo lectura por el API interno.
    """
    active_lower = [name.lower() for name in active_supplier_names]
    for group in EXCLUSION_GROUPS:
        if not group["enabled"]:
            continue
        if group["buyer_name_contains"].lower() not in buyer_name.lower():
            continue
        # Coincidencia por substring, no igualdad exacta: las allocations
        # reales traen client_supplier__name completo (ej. "Prime API"), no
        # el nombre corto de la config (ej. "Prime").
        matched_suppliers = {
            excluded
            for excluded in group["mutually_exclusive_suppliers"]
            if any(excluded.lower() in active_name for active_name in active_lower)
        }
        if len(matched_suppliers) >= 2:
            return ExclusionCheckResult(
                passed=False,
                triggered_rule_id=group["rule_id"],
                reason=f"proveedores en conflicto activos a la vez: {sorted(matched_suppliers)}",
            )
    return ExclusionCheckResult(passed=True, triggered_rule_id=None, reason="sin conflictos")


@dataclass(frozen=True)
class RankingScopeResult:
    in_scope: bool
    reason: str


def check_ranking_scope(type_industry_name: str | None) -> RankingScopeResult:
    """
    Fail-safe: type_industry_name vacio o desconocido -> in_scope=False. Nunca
    se incluye por defecto una categoria que no se pudo determinar.
    """
    if not type_industry_name:
        return RankingScopeResult(in_scope=False, reason="missing_type_industry")
    excluded = {name.lower() for name in RANKING_EXCLUDED_TYPE_INDUSTRIES}
    if type_industry_name.lower() in excluded:
        return RankingScopeResult(in_scope=False, reason=f"categoria excluida: {type_industry_name}")
    return RankingScopeResult(in_scope=True, reason="dentro de alcance")


@dataclass(frozen=True)
class CpiSuggestion:
    suggested_cpi: float | None
    status: str
    reason: str


def calculate_profit_per_complete(cpi_client: float, cpi_paid: float) -> float:
    """
    Ganancia en dolares por cada panelista completado: cpi_client - cpi_paid,
    recortado a >=0 (misma filosofia que calculate_profit_percent -- nunca se
    reporta una ganancia negativa como si fuera perdida contable exacta,
    solo "no hay ganancia en este complete").
    """
    if cpi_client is None or cpi_paid is None:
        return 0.0
    return max(cpi_client - cpi_paid, 0.0)


def calculate_termination_rate(terms_count: int, prescreens_count: int) -> float | None:
    """
    % de quienes pasaron el prescreen y terminaron sin completar. Devuelve
    None si prescreens_count es 0 (nadie con quien calcular una tasa todavia
    -- distinto de 0%, que significaria "nadie termino").
    """
    if not prescreens_count:
        return None
    return round((terms_count / prescreens_count) * 100, 1)


@dataclass(frozen=True)
class AlarmCheckResult:
    triggered: bool
    value: float | None
    threshold: float
    reason: str


def check_ir_alarm(ir: float | None, threshold: float = IR_ALARM_THRESHOLD_PERCENT) -> AlarmCheckResult:
    """
    IR es el % medido en vivo (no el target de la encuesta). None = todavia
    sin datos suficientes -- no se alarma sobre lo que no se puede medir.
    """
    if ir is None:
        return AlarmCheckResult(triggered=False, value=None, threshold=threshold, reason="sin datos de IR todavia")
    if ir <= threshold:
        return AlarmCheckResult(triggered=True, value=ir, threshold=threshold, reason=f"IR {ir}% <= umbral {threshold}%")
    return AlarmCheckResult(triggered=False, value=ir, threshold=threshold, reason=f"IR {ir}% > umbral {threshold}%")


def check_termination_alarm(
    terms_count: int, prescreens_count: int, threshold: float = TERMINATION_ALARM_THRESHOLD_PERCENT
) -> AlarmCheckResult:
    rate = calculate_termination_rate(terms_count, prescreens_count)
    if rate is None:
        return AlarmCheckResult(triggered=False, value=None, threshold=threshold, reason="sin prescreens todavia")
    if rate >= threshold:
        return AlarmCheckResult(triggered=True, value=rate, threshold=threshold, reason=f"terminacion {rate}% >= umbral {threshold}%")
    return AlarmCheckResult(triggered=False, value=rate, threshold=threshold, reason=f"terminacion {rate}% < umbral {threshold}%")


def suggest_cpi(**kwargs) -> CpiSuggestion:
    """
    Siempre devuelve suggested_cpi=None, status="needs_input". La formula de
    pricing todavia no esta confirmada con Piyush (README, "Still open") -- es
    un stub deliberado, no un placeholder para completar en silencio. Cuando
    la formula se confirme, este es el unico lugar que cambia, y el test que
    hoy afirma None debe actualizarse a proposito, no accidentalmente.
    """
    return CpiSuggestion(
        suggested_cpi=None,
        status="needs_input",
        reason="formula de CPI aun no confirmada con Piyush",
    )
