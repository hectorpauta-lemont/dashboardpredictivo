from django.db import models


class AwardedSurvey(models.Model):
    """
    Estado normalizado actual de una encuesta AWARDED en Link -- una fila por
    survey_supplier_id, se sobrescribe en cada poll. Mismo rol que
    ScreenedSurvey en eros-prime-gate: "que sabemos ahora", no historial (eso
    es audit.DecisionLog).
    """

    survey_supplier_id = models.CharField(max_length=64, unique=True, db_index=True)
    survey_name = models.CharField(max_length=255, blank=True, default="")
    project_id = models.CharField(max_length=64, blank=True, default="")
    buyer_client_id = models.CharField(max_length=64, blank=True, default="")
    buyer_name = models.CharField(max_length=255, blank=True, default="", db_index=True)
    type_industry_name = models.CharField(max_length=100, blank=True, default="")
    status_name = models.CharField(max_length=32, blank=True, default="")

    # Fecha de creacion real de la encuesta en Link (viene de
    # SurveySupplier.date_created) -- distinta de first_seen_at/last_polled_at,
    # que son cuando NUESTRO poller la vio, no cuando se creo en Link. Es la
    # que hace falta para un filtro de rango de fechas comparable 1:1 con lo
    # que muestra el propio Link.
    survey_created_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # cpi/cpi_client de SurveySupplier en Link -- suelen estar en 0 mientras
    # la encuesta sigue AWARDED (Piyush todavia no le pone precio a mano).
    cpi = models.FloatField(null=True, blank=True)
    cpi_client = models.FloatField(null=True, blank=True)
    ir = models.PositiveIntegerField(null=True, blank=True)
    loi = models.PositiveIntegerField(null=True, blank=True)
    noc = models.PositiveIntegerField(null=True, blank=True)

    # Deliberadamente NO se promueve survey_supplier.profit_percentage (campo
    # de Link, default 50) a columna propia -- es distinto del piso de 30% de
    # negocio y facil de confundir por el nombre. Queda solo dentro del JSON
    # crudo, para trazabilidad.
    raw_survey_supplier = models.JSONField(default=dict)
    raw_allocations = models.JSONField(default=list)

    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_polled_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_polled_at"]

    def __str__(self):
        return f"{self.survey_supplier_id} ({self.survey_name})"


class LiveSurveySnapshot(models.Model):
    """
    Append-only, igual que ScreeningLog en eros-prime-gate: una fila por cada
    vez que se sondea una encuesta LIVE. NO se sobrescribe -- es lo que
    permite calcular "cuanto se gano desde el primer snapshot del dia", ya
    que sin historial no hay forma de saber cuantos completes eran nuevos.
    """

    survey_supplier_id = models.CharField(max_length=64, db_index=True)
    survey_name = models.CharField(max_length=255, blank=True, default="")
    buyer_name = models.CharField(max_length=255, blank=True, default="")
    cpi = models.FloatField(null=True, blank=True)
    cpi_client = models.FloatField(null=True, blank=True)
    completes = models.PositiveIntegerField(default=0)
    quota = models.PositiveIntegerField(default=0)
    profit_per_complete = models.FloatField(null=True, blank=True)

    # Metricas de performance en vivo -- mismos campos que ve Piyush en Link
    # (verificado 2026-08-18 contra la encuesta real que reporto: entrants=19,
    # terms=15, ir=0, perc_conversion=0 coincide exacto con su captura).
    # "ir" aqui es el % medido en vivo, NO el ir de AwardedSurvey (ese es el
    # target/config de la encuesta, no el resultado real).
    ir = models.FloatField(null=True, blank=True)
    perc_conversion = models.FloatField(null=True, blank=True)
    perc_drop_off = models.FloatField(null=True, blank=True)
    entrants_count = models.PositiveIntegerField(default=0)
    prescreens_count = models.PositiveIntegerField(default=0)
    terms_count = models.PositiveIntegerField(default=0)

    # Igual que AwardedSurvey.raw_allocations -- resultado crudo de
    # get_allocations_for_survey (requiere LINK_JWT_TOKEN, ver
    # intake/link_client.py). Solo el ranking de "mejores suppliers" lo usa,
    # y solo con el snapshot MAS RECIENTE por encuesta (las stats de Link ya
    # vienen acumuladas, no hay que sumar entre snapshots).
    raw_allocations = models.JSONField(default=list)

    polled_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-polled_at"]
        indexes = [models.Index(fields=["survey_supplier_id", "-polled_at"])]

    def __str__(self):
        return f"{self.survey_supplier_id} @ {self.polled_at}: {self.completes}/{self.quota}"
