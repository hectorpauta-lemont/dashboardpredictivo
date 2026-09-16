"""
Punto de entrada publico unico de rules_engine. Combina los cuatro chequeos de
checks.py en un veredicto, con un orden de precedencia explicito y probado --
ese orden es visible para el negocio (es lo primero que ve Piyush), no un
accidente de como itera un dict.
"""

from dataclasses import dataclass

from rules_engine.checks import (
    CpiSuggestion,
    ExclusionCheckResult,
    ProfitCheckResult,
    RankingScopeResult,
    apply_profit_floor,
    check_exclusion_conflicts,
    check_ranking_scope,
    suggest_cpi,
)


@dataclass(frozen=True)
class SurveyEvaluationInput:
    survey_supplier_id: str
    buyer_name: str
    type_industry_name: str | None
    cpi: float
    cpi_client: float
    active_allocation_supplier_names: list[str]


class Verdict:
    OUT_OF_SCOPE = "out_of_scope"
    BLOCKED_EXCLUSION = "blocked_exclusion"
    BLOCKED_PROFIT_FLOOR = "blocked_profit_floor"
    ELIGIBLE_NEEDS_CPI_INPUT = "eligible_needs_cpi_input"


@dataclass(frozen=True)
class SurveyDecision:
    survey_supplier_id: str
    verdict: str
    profit_check: ProfitCheckResult
    exclusion_check: ExclusionCheckResult
    ranking_check: RankingScopeResult
    cpi_suggestion: CpiSuggestion


def evaluate_survey(data: SurveyEvaluationInput) -> SurveyDecision:
    ranking_check = check_ranking_scope(data.type_industry_name)
    exclusion_check = check_exclusion_conflicts(data.buyer_name, data.active_allocation_supplier_names)
    profit_check = apply_profit_floor(data.cpi_client, data.cpi)
    cpi_suggestion = suggest_cpi()

    # Precedencia, de mas a menos severo: fuera de alcance > conflicto de
    # exclusion > no llega al piso de rentabilidad > elegible (falta CPI).
    if not ranking_check.in_scope:
        verdict = Verdict.OUT_OF_SCOPE
    elif not exclusion_check.passed:
        verdict = Verdict.BLOCKED_EXCLUSION
    elif not profit_check.passed:
        verdict = Verdict.BLOCKED_PROFIT_FLOOR
    else:
        verdict = Verdict.ELIGIBLE_NEEDS_CPI_INPUT

    return SurveyDecision(
        survey_supplier_id=data.survey_supplier_id,
        verdict=verdict,
        profit_check=profit_check,
        exclusion_check=exclusion_check,
        ranking_check=ranking_check,
        cpi_suggestion=cpi_suggestion,
    )
