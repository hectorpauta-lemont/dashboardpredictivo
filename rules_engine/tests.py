"""
Unit tests puros para rules_engine -- sin DB, sin red. Los fixtures usan los
mismos nombres de campo que tnr-link real (cpi, cpi_client, profit_percentage,
custom_cpi) para que la matematica coincida con lo que produccion calcularia.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from rules_engine.checks import (
    apply_profit_floor,
    calculate_profit_per_complete,
    calculate_profit_percent,
    calculate_termination_rate,
    check_exclusion_conflicts,
    check_ir_alarm,
    check_ranking_scope,
    check_termination_alarm,
    suggest_cpi,
)
from rules_engine.engine import SurveyEvaluationInput, Verdict, evaluate_survey
from rules_engine.rules_config import EXCLUSION_GROUPS


class ProfitFloorTests(SimpleTestCase):
    def test_exact_floor_passes(self):
        # cpi_client=10, cpi_paid=7 -> 30% exacto. El piso es inclusive (>=).
        result = apply_profit_floor(cpi_client=10, cpi_paid=7)
        self.assertTrue(result.passed)
        self.assertEqual(result.profit_percent, 30.0)

    def test_below_floor_fails(self):
        # cpi_client=10, cpi_paid=7.5 -> 25%.
        result = apply_profit_floor(cpi_client=10, cpi_paid=7.5)
        self.assertFalse(result.passed)
        self.assertEqual(result.profit_percent, 25.0)

    def test_well_above_floor_passes(self):
        result = apply_profit_floor(cpi_client=10, cpi_paid=3)
        self.assertTrue(result.passed)
        self.assertEqual(result.profit_percent, 70.0)

    def test_cpi_client_zero_returns_none_never_a_pass(self):
        result = apply_profit_floor(cpi_client=0, cpi_paid=5)
        self.assertIsNone(result.profit_percent)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "cpi_client_missing_or_zero")

    def test_negative_raw_profit_is_clamped_to_zero(self):
        # cpi_paid > cpi_client -> profit crudo negativo. Se recorta a 0, no a
        # negativo -- replica AllocationsListAPIView._calculate_profit, NO la
        # otra formula de tnr-link (apps/router/core/utils.calculate_profit)
        # que si permite negativos.
        percent = calculate_profit_percent(cpi_client=10, cpi_paid=15)
        self.assertEqual(percent, 0.0)

    def test_round_half_to_even_30_stays_30(self):
        # cpi_client=200, cpi_paid=141 -> crudo 29.5 -> round(29.5, 0) == 30
        # (30 es par). Comportamiento real de Python, coincide con lo que
        # calcularia el mismo endpoint de Link.
        result = apply_profit_floor(cpi_client=200, cpi_paid=141)
        self.assertEqual(result.profit_percent, 30.0)
        self.assertTrue(result.passed)

    def test_round_half_to_even_28_5_stays_28(self):
        # cpi_client=200, cpi_paid=143 -> crudo 28.5 -> round(28.5, 0) == 28
        # (28 es par).
        result = apply_profit_floor(cpi_client=200, cpi_paid=143)
        self.assertEqual(result.profit_percent, 28.0)
        self.assertFalse(result.passed)

    def test_survey_supplier_profit_percentage_field_is_not_the_floor(self):
        # Regresion: si alguien alguna vez lee survey.profit_percentage (el
        # campo de Link, default 50) en vez de PROFIT_FLOOR_PERCENT, este
        # fixture con profit real de 29% debe seguir fallando el piso.
        result = apply_profit_floor(cpi_client=100, cpi_paid=71)
        self.assertEqual(result.profit_percent, 29.0)
        self.assertFalse(result.passed)


class ProfitPerCompleteTests(SimpleTestCase):
    def test_real_link_example(self):
        # cpi=1.22, cpi_client=1.74 -- encuesta LIVE real confirmada el
        # 2026-08-18. Ganancia esperada: 0.52 por completado.
        self.assertAlmostEqual(calculate_profit_per_complete(1.74, 1.22), 0.52, places=6)

    def test_negative_is_clamped_to_zero(self):
        self.assertEqual(calculate_profit_per_complete(1.0, 2.0), 0.0)

    def test_none_values_return_zero_never_crash(self):
        self.assertEqual(calculate_profit_per_complete(None, 1.0), 0.0)
        self.assertEqual(calculate_profit_per_complete(1.0, None), 0.0)


class PerformanceAlarmTests(SimpleTestCase):
    def test_real_reported_survey_triggers_both_alarms(self):
        # SY AH EN 8450128, reportada por Piyush el 2026-08-19: entrants=19,
        # prescreens=18, terms=15, ir=0, perc_conversion=0 -- verificado
        # contra la API real, no inventado.
        ir_result = check_ir_alarm(ir=0)
        self.assertTrue(ir_result.triggered)

        term_rate = calculate_termination_rate(terms_count=15, prescreens_count=18)
        self.assertAlmostEqual(term_rate, 83.3, places=1)
        term_result = check_termination_alarm(terms_count=15, prescreens_count=18)
        self.assertTrue(term_result.triggered)

    def test_ir_above_threshold_does_not_trigger(self):
        self.assertFalse(check_ir_alarm(ir=25).triggered)

    def test_ir_exactly_at_threshold_triggers_inclusive(self):
        self.assertTrue(check_ir_alarm(ir=11).triggered)

    def test_ir_none_never_triggers(self):
        result = check_ir_alarm(ir=None)
        self.assertFalse(result.triggered)
        self.assertIsNone(result.value)

    def test_termination_below_threshold_does_not_trigger(self):
        self.assertFalse(check_termination_alarm(terms_count=5, prescreens_count=20).triggered)  # 25%

    def test_termination_no_prescreens_yet_never_triggers(self):
        result = check_termination_alarm(terms_count=0, prescreens_count=0)
        self.assertFalse(result.triggered)
        self.assertIsNone(result.value)


class ExclusionConflictTests(SimpleTestCase):
    def test_shipped_config_always_passes_default_is_disabled(self):
        # Canary: la config tal como se distribuye tiene enabled=False, asi
        # que ningun input deberia disparar un bloqueo todavia.
        self.assertFalse(EXCLUSION_GROUPS[0]["enabled"])
        result = check_exclusion_conflicts("Buyer 42 Corp", ["Prime API", "Lucid Marketplace"])
        self.assertTrue(result.passed)

    @patch(
        "rules_engine.checks.EXCLUSION_GROUPS",
        [
            {
                "rule_id": "prime_vs_lucid_cint_buyer42",
                "buyer_name_contains": "Buyer 42",
                "mutually_exclusive_suppliers": ["Prime", "Lucid", "Cint"],
                "enabled": True,
            }
        ],
    )
    def test_both_conflicting_suppliers_active_blocks(self):
        result = check_exclusion_conflicts("Buyer 42 Corp", ["Prime API", "Lucid Marketplace"])
        self.assertFalse(result.passed)
        self.assertEqual(result.triggered_rule_id, "prime_vs_lucid_cint_buyer42")

    @patch(
        "rules_engine.checks.EXCLUSION_GROUPS",
        [
            {
                "rule_id": "prime_vs_lucid_cint_buyer42",
                "buyer_name_contains": "Buyer 42",
                "mutually_exclusive_suppliers": ["Prime", "Lucid", "Cint"],
                "enabled": True,
            }
        ],
    )
    def test_only_one_conflicting_supplier_active_passes(self):
        result = check_exclusion_conflicts("Buyer 42 Corp", ["Prime API"])
        self.assertTrue(result.passed)

    @patch(
        "rules_engine.checks.EXCLUSION_GROUPS",
        [
            {
                "rule_id": "prime_vs_lucid_cint_buyer42",
                "buyer_name_contains": "Buyer 42",
                "mutually_exclusive_suppliers": ["Prime", "Lucid", "Cint"],
                "enabled": True,
            }
        ],
    )
    def test_different_buyer_not_scoped_passes(self):
        result = check_exclusion_conflicts("Symmetric Corp", ["Prime API", "Lucid Marketplace"])
        self.assertTrue(result.passed)


class RankingScopeTests(SimpleTestCase):
    def test_beauty_excluded_case_insensitive(self):
        for value in ["Beauty", "beauty", "BEAUTY"]:
            with self.subTest(value=value):
                self.assertFalse(check_ranking_scope(value).in_scope)

    def test_consumer_category_in_scope(self):
        self.assertTrue(check_ranking_scope("Consumer Electronics").in_scope)

    def test_blank_or_none_is_fail_safe_out_of_scope(self):
        for value in [None, ""]:
            with self.subTest(value=value):
                result = check_ranking_scope(value)
                self.assertFalse(result.in_scope)
                self.assertEqual(result.reason, "missing_type_industry")


class CpiSuggestionTests(SimpleTestCase):
    def test_always_needs_input_until_formula_confirmed(self):
        result = suggest_cpi()
        self.assertIsNone(result.suggested_cpi)
        self.assertEqual(result.status, "needs_input")


class EvaluateSurveyPrecedenceTests(SimpleTestCase):
    def _make_input(self, **overrides):
        defaults = dict(
            survey_supplier_id="s-1",
            buyer_name="Symmetric Corp",
            type_industry_name="Consumer Electronics",
            cpi=3,
            cpi_client=10,
            active_allocation_supplier_names=[],
        )
        defaults.update(overrides)
        return SurveyEvaluationInput(**defaults)

    def test_eligible_when_everything_passes(self):
        decision = evaluate_survey(self._make_input())
        self.assertEqual(decision.verdict, Verdict.ELIGIBLE_NEEDS_CPI_INPUT)

    def test_out_of_scope_wins_even_if_profit_also_fails(self):
        data = self._make_input(type_industry_name="beauty", cpi=9, cpi_client=10)
        decision = evaluate_survey(data)
        self.assertEqual(decision.verdict, Verdict.OUT_OF_SCOPE)

    def test_blocked_profit_floor_when_in_scope_and_no_exclusion(self):
        data = self._make_input(cpi=9, cpi_client=10)  # 10% profit, bajo el piso
        decision = evaluate_survey(data)
        self.assertEqual(decision.verdict, Verdict.BLOCKED_PROFIT_FLOOR)

    @patch(
        "rules_engine.checks.EXCLUSION_GROUPS",
        [
            {
                "rule_id": "prime_vs_lucid_cint_buyer42",
                "buyer_name_contains": "Buyer 42",
                "mutually_exclusive_suppliers": ["Prime", "Lucid", "Cint"],
                "enabled": True,
            }
        ],
    )
    def test_exclusion_wins_over_profit_floor(self):
        # En alcance, con conflicto de exclusion Y profit bajo el piso -- la
        # exclusion debe ganar (mas severa que un piso de rentabilidad).
        data = self._make_input(
            buyer_name="Buyer 42 Corp",
            active_allocation_supplier_names=["Prime API", "Lucid Marketplace"],
            cpi=9,
            cpi_client=10,
        )
        decision = evaluate_survey(data)
        self.assertEqual(decision.verdict, Verdict.BLOCKED_EXCLUSION)
