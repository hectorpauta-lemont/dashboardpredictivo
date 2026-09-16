from django.db import models


class AllocatorRuntimeState(models.Model):
    """Fila singleton por worker -- heartbeat/estado, mismo rol que
    ScreeningRuntimeState en eros-prime-gate. Sirve para health checks."""

    name = models.CharField(max_length=32, unique=True, default="main")

    class ProcessStatus(models.TextChoices):
        STARTING = "starting", "Starting"
        RUNNING = "running", "Running"
        STOPPED = "stopped", "Stopped"
        ERROR = "error", "Error"

    class TokenStatus(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        VALID = "valid", "Valid"
        INVALID = "invalid", "Invalid"
        MISSING = "missing", "Missing"

    process_status = models.CharField(
        max_length=16, choices=ProcessStatus.choices, default=ProcessStatus.STARTING
    )
    token_status = models.CharField(
        max_length=16, choices=TokenStatus.choices, default=TokenStatus.UNKNOWN
    )
    process_started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    last_poll_attempt_at = models.DateTimeField(null=True, blank=True)
    last_poll_success_at = models.DateTimeField(null=True, blank=True)
    last_poll_error = models.CharField(max_length=500, blank=True, default="")
    last_surveys_seen_count = models.PositiveIntegerField(default=0)
    last_evaluated_count = models.PositiveIntegerField(default=0)
    last_evaluated_survey_supplier_id = models.CharField(max_length=64, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name}: {self.process_status}/{self.token_status}"


class PerformanceAlarm(models.Model):
    """
    Una fila activa por (encuesta, tipo de alarma) -- evita mandar el mismo
    aviso de Telegram/email cada 5 minutos mientras el problema sigue igual.
    Se marca resuelta cuando la metrica vuelve a estar por encima del umbral;
    si vuelve a caer despues, se crea una fila nueva (no se reabre la vieja,
    para conservar el historial de cuantas veces paso).
    """

    class AlarmType(models.TextChoices):
        IR_LOW = "ir_low", "IR bajo"
        TERMINATION_HIGH = "termination_high", "Terminacion alta"

    survey_supplier_id = models.CharField(max_length=64, db_index=True)
    survey_name = models.CharField(max_length=255, blank=True, default="")
    buyer_name = models.CharField(max_length=255, blank=True, default="")
    alarm_type = models.CharField(max_length=32, choices=AlarmType.choices)
    value_at_trigger = models.FloatField(null=True, blank=True)
    threshold = models.FloatField()
    is_active = models.BooleanField(default=True)
    triggered_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    notified_email = models.CharField(max_length=16, default="not_attempted")
    notified_telegram = models.CharField(max_length=16, default="not_attempted")

    class Meta:
        ordering = ["-triggered_at"]
        indexes = [models.Index(fields=["survey_supplier_id", "alarm_type", "is_active"])]

    def __str__(self):
        return f"{self.get_alarm_type_display()} -- {self.survey_supplier_id} ({'activa' if self.is_active else 'resuelta'})"


class DailyMetricRollup(models.Model):
    """
    Resumen diario append-only/upsert -- una fila por (dia, buyer, proveedor).
    Lo llena el comando `build_dashboard_rollups` (cron 1x/dia), no un request.
    El dashboard lo lee para ventanas largas (meses de historia, estilo Power BI)
    en vez de escanear miles de LiveSurveySnapshot en cada llamada.

    'day' es la fecha local. Los valores son la ACTIVIDAD de ese dia
    (ultimo snapshot del dia - primero), mismo criterio que BusinessSummaryView.
    """

    day = models.DateField(db_index=True)
    buyer_name = models.CharField(max_length=255, blank=True, default="")
    supplier_name = models.CharField(max_length=255, blank=True, default="")

    surveys = models.PositiveIntegerField(default=0)
    entrants = models.PositiveIntegerField(default=0)
    completed = models.PositiveIntegerField(default=0)
    terminated = models.PositiveIntegerField(default=0)
    prescreens = models.PositiveIntegerField(default=0)
    revenue = models.FloatField(default=0.0)
    cost = models.FloatField(default=0.0)
    profit = models.FloatField(default=0.0)

    # Para reconstruir el promedio de IR ponderado: sum(ir) y cantidad.
    ir_sum = models.FloatField(default=0.0)
    ir_n = models.PositiveIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-day"]
        constraints = [
            models.UniqueConstraint(
                fields=["day", "buyer_name", "supplier_name"], name="uniq_rollup_day_buyer_supplier"
            )
        ]
        indexes = [models.Index(fields=["day", "buyer_name"])]

    def __str__(self):
        return f"{self.day} {self.buyer_name}/{self.supplier_name}: {self.completed} completes"
