from django.db import models


class DecisionLog(models.Model):
    """
    Append-only -- nunca se modifica ni se borra. Una fila por cada
    evaluacion de rules_engine sobre una encuesta en un momento dado (una
    encuesta acumula muchas filas a lo largo de sucesivos polls, misma
    relacion que ScreeningLog tiene con ScreenedSurvey en eros-prime-gate).
    Este es el log de decisiones exigido por la gobernanza del README y el
    mecanismo de backtesting contra lo que Piyush decidiria a mano.
    """

    survey = models.ForeignKey(
        "intake.AwardedSurvey", on_delete=models.CASCADE, related_name="decisions"
    )

    # Copias congeladas al momento de evaluar -- buyer_name/type_industry_name
    # del survey podrian cambiar despues; esta fila no debe cambiar con ellos.
    buyer_name = models.CharField(max_length=255, blank=True, default="")
    type_industry_name = models.CharField(max_length=100, blank=True, default="")

    class Verdict(models.TextChoices):
        ELIGIBLE_NEEDS_CPI_INPUT = "eligible_needs_cpi_input", "Eligible, needs CPI input"
        BLOCKED_PROFIT_FLOOR = "blocked_profit_floor", "Blocked: profit floor"
        BLOCKED_EXCLUSION = "blocked_exclusion", "Blocked: exclusion conflict"
        OUT_OF_SCOPE = "out_of_scope", "Out of ranking scope"

    verdict = models.CharField(max_length=32, choices=Verdict.choices)

    # Inputs, con los mismos nombres que usa tnr-link.
    cpi = models.FloatField(null=True, blank=True)
    cpi_client = models.FloatField(null=True, blank=True)
    profit_floor_used = models.FloatField(null=True, blank=True)

    # Outputs
    computed_profit_percent = models.FloatField(null=True, blank=True)
    profit_floor_passed = models.BooleanField(default=False)
    exclusion_rule_triggered = models.CharField(max_length=100, blank=True, default="")
    exclusion_passed = models.BooleanField(default=False)
    ranking_in_scope = models.BooleanField(default=False)
    cpi_suggestion_status = models.CharField(max_length=32, default="needs_input")

    raw_input = models.JSONField(default=dict)
    raw_decision = models.JSONField(default=dict)

    evaluated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-evaluated_at"]
        indexes = [
            models.Index(fields=["survey", "-evaluated_at"]),
            models.Index(fields=["evaluated_at"]),
        ]

    def __str__(self):
        return f"{self.survey_id} @ {self.evaluated_at}: {self.verdict}"
