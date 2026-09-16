"""
Sondea encuestas LIVE reales de Link y guarda un snapshot append-only de su
progreso (completados/cupo) y ganancia por completado. Nunca escribe a Link.

Uso:
    python manage.py run_earnings_tracker --once --limit 20
    python manage.py run_earnings_tracker --hours 1 --limit 30 --poll-interval 60
"""

import logging
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import DatabaseError
from django.utils import timezone

from intake.link_client import (
    LinkInternalApiError,
    get_allocations_for_survey,
    get_live_survey_suppliers,
    get_survey_supplier_detail,
)
from intake.models import LiveSurveySnapshot
from orchestrator.models import AllocatorRuntimeState, PerformanceAlarm
from orchestrator.notifications import notify_performance_alarm
from rules_engine.checks import calculate_profit_per_complete, check_ir_alarm, check_termination_alarm

logger = logging.getLogger(__name__)

RUNTIME_STATE_NAME = "earnings"


class Command(BaseCommand):
    help = "Sondea encuestas LIVE de Link y guarda snapshots de progreso/ganancia. Nunca escribe a Link."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=float, default=1.0)
        parser.add_argument("--limit", type=int, default=200, help="Encuestas LIVE por pasada (default 200 -- cubre todas las activas hoy, ~145; cada una implica una llamada extra de detalle)")
        parser.add_argument("--delay", type=float, default=0.3)
        parser.add_argument("--poll-interval", type=float, default=300.0, help="Segundos entre pasadas completas (default 300 = 5 min)")
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        token = settings.LINK_API_TOKEN
        if not token:
            self._update_runtime_state(
                process_status=AllocatorRuntimeState.ProcessStatus.ERROR,
                token_status=AllocatorRuntimeState.TokenStatus.MISSING,
                heartbeat_at=timezone.now(),
            )
            self.stderr.write(self.style.ERROR("Falta LINK_API_TOKEN en .env."))
            return

        hours = options["hours"]
        limit = options["limit"]
        delay = options["delay"]
        poll_interval = options["poll_interval"]
        once = options["once"]

        deadline = timezone.now() + timedelta(hours=hours)
        started_at = timezone.now()
        self._update_runtime_state(
            process_status=AllocatorRuntimeState.ProcessStatus.RUNNING,
            token_status=AllocatorRuntimeState.TokenStatus.UNKNOWN,
            process_started_at=started_at,
            heartbeat_at=started_at,
            last_poll_error="",
        )
        self.stdout.write(self.style.SUCCESS(f"Earnings tracker iniciado -- limite/pasada={limit}"))

        total_snapshots = 0
        while True:
            poll_at = timezone.now()
            self._update_runtime_state(heartbeat_at=poll_at, last_poll_attempt_at=poll_at)

            try:
                live_rows = get_live_survey_suppliers(token=token, limit=limit)
            except LinkInternalApiError as e:
                token_status = (
                    AllocatorRuntimeState.TokenStatus.INVALID
                    if e.status_code in {401, 403}
                    else AllocatorRuntimeState.TokenStatus.UNKNOWN
                )
                self._update_runtime_state(
                    heartbeat_at=timezone.now(), last_poll_error=f"Link API HTTP {e.status_code}", token_status=token_status
                )
                self.stderr.write(self.style.WARNING(f"Link API error: {e}"))
                if once:
                    break
                time.sleep(poll_interval)
                if timezone.now() >= deadline:
                    break
                continue

            self._update_runtime_state(
                heartbeat_at=timezone.now(),
                last_poll_success_at=timezone.now(),
                last_poll_error="",
                last_surveys_seen_count=len(live_rows),
                token_status=AllocatorRuntimeState.TokenStatus.VALID,
            )
            self.stdout.write(f"LIVE encontradas: {len(live_rows)}")

            snapshots_this_pass = 0
            pending_snapshots: list[LiveSurveySnapshot] = []
            for row in live_rows:
                if timezone.now() >= deadline and not once:
                    break
                try:
                    pending_snapshots.append(self._snapshot_one(row, token))
                    snapshots_this_pass += 1
                    total_snapshots += 1
                except Exception:
                    logger.exception(
                        "Live survey snapshot failed",
                        extra={"event": "earnings_snapshot_failed", "survey_supplier_id": row.get("id")},
                    )
                    self.stderr.write(self.style.WARNING(f"Error en {row.get('id')}"))
                time.sleep(delay)

            # Un solo INSERT masivo para toda la pasada -- ver docstring de
            # _snapshot_one.
            if pending_snapshots:
                LiveSurveySnapshot.objects.bulk_create(pending_snapshots)

            self._update_runtime_state(heartbeat_at=timezone.now(), last_evaluated_count=snapshots_this_pass)

            if once or timezone.now() >= deadline:
                break
            time.sleep(poll_interval)

        self._update_runtime_state(process_status=AllocatorRuntimeState.ProcessStatus.STOPPED, heartbeat_at=timezone.now())
        self.stdout.write(self.style.SUCCESS(f"Earnings tracker terminado -- {total_snapshots} snapshots."))

    def _snapshot_one(self, row: dict, token: str) -> LiveSurveySnapshot:
        """
        Solo hace las llamadas de red (lentas) y arma el snapshot EN MEMORIA
        -- no escribe a la base todavia. El caller junta todos los snapshots
        de la pasada y los graba de una sola vez con bulk_create (ver
        handle()). Antes cada encuesta hacia su propio INSERT/commit --
        con ~150 encuestas por pasada eso son ~150 transacciones separadas
        compitiendo por el lock de sqlite contra run_allocator corriendo en
        simultaneo (confirmado 2026-08-27: consultas triviales del panel
        tardaban 5-10x mas de lo normal por esta contencion). Una sola
        transaccion al final del bulk_create es casi instantanea en
        comparacion.
        """
        survey_supplier_id = row["id"]
        survey_name = row.get("name", "")
        buyer_name = row.get("project_client_name", "")
        detail = get_survey_supplier_detail(survey_supplier_id, token)
        cpi = detail.get("cpi")
        cpi_client = detail.get("cpi_client")
        profit_per_complete = calculate_profit_per_complete(cpi_client, cpi)
        ir = row.get("ir")
        prescreens_count = row.get("qty_progress_prescreens") or 0
        terms_count = row.get("terms_count") or 0

        try:
            allocations = get_allocations_for_survey(survey_supplier_id, settings.LINK_JWT_TOKEN)
        except LinkInternalApiError:
            # LINK_JWT_TOKEN vacio o vencido -- ver intake/link_client.py. No
            # es fatal para el snapshot de ganancias, solo el ranking de
            # suppliers se queda sin este dato puntual.
            allocations = []

        snapshot = LiveSurveySnapshot(
            survey_supplier_id=survey_supplier_id,
            survey_name=survey_name,
            buyer_name=buyer_name,
            cpi=cpi,
            cpi_client=cpi_client,
            completes=row.get("qty_progress_completes") or 0,
            quota=row.get("qty_progress_quota") or 0,
            profit_per_complete=profit_per_complete,
            ir=ir,
            perc_conversion=row.get("perc_conversion"),
            perc_drop_off=row.get("perc_drop_off"),
            entrants_count=row.get("entrants_count") or 0,
            prescreens_count=prescreens_count,
            terms_count=terms_count,
            raw_allocations=allocations,
        )
        self.stdout.write(
            f"{survey_name}: {row.get('qty_progress_completes')}/{row.get('qty_progress_quota')} "
            f"-- ${profit_per_complete:.2f}/complete -- IR={ir}% term={terms_count}/{prescreens_count}"
        )

        # Las alarmas si escriben aparte (volumen bajo -- solo cuando algo
        # cruza el umbral -- no vale la pena batchearlas tambien).
        self._check_alarm(
            survey_supplier_id, survey_name, buyer_name,
            PerformanceAlarm.AlarmType.IR_LOW, check_ir_alarm(ir),
        )
        self._check_alarm(
            survey_supplier_id, survey_name, buyer_name,
            PerformanceAlarm.AlarmType.TERMINATION_HIGH, check_termination_alarm(terms_count, prescreens_count),
        )
        return snapshot

    def _check_alarm(self, survey_supplier_id, survey_name, buyer_name, alarm_type, result) -> None:
        """
        Solo una fila activa por (encuesta, tipo) -- si ya hay una alarma
        activa y sigue disparada, no se manda otro aviso cada 5 minutos. Si
        se recupera, se marca resuelta (sin aviso de recuperacion por ahora).
        """
        existing = PerformanceAlarm.objects.filter(
            survey_supplier_id=survey_supplier_id, alarm_type=alarm_type, is_active=True
        ).first()

        if not result.triggered:
            if existing:
                existing.is_active = False
                existing.resolved_at = timezone.now()
                existing.save(update_fields=["is_active", "resolved_at"])
            return

        if existing:
            return  # ya esta activa y ya se notifico -- no repetir

        alarm = PerformanceAlarm.objects.create(
            survey_supplier_id=survey_supplier_id,
            survey_name=survey_name,
            buyer_name=buyer_name,
            alarm_type=alarm_type,
            value_at_trigger=result.value,
            threshold=result.threshold,
        )
        self.stdout.write(self.style.WARNING(f"ALARMA {alarm.get_alarm_type_display()} -- {survey_name}: {result.reason}"))
        # En thread aparte -- email/Telegram pueden tardar y no deben frenar
        # el loop principal de sondeo (mismo criterio que
        # notify_zamplia_blocked en eros-prime-gate).
        threading.Thread(target=self._notify_and_record, args=(alarm,), daemon=True).start()

    def _notify_and_record(self, alarm: PerformanceAlarm) -> None:
        results = notify_performance_alarm(alarm)
        PerformanceAlarm.objects.filter(pk=alarm.pk).update(
            notified_email=results.get("email", "not_attempted"),
            notified_telegram=results.get("telegram", "not_attempted"),
        )

    def _update_runtime_state(self, **fields) -> None:
        try:
            AllocatorRuntimeState.objects.update_or_create(name=RUNTIME_STATE_NAME, defaults=fields)
        except DatabaseError:
            logger.exception("Could not persist earnings runtime state", extra={"event": "earnings_state_update_failed"})
