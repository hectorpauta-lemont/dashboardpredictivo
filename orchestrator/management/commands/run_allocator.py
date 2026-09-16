"""
Comando principal de Milestone 1: sondea encuestas AWARDED reales de Link,
las evalua con rules_engine, y registra cada decision en audit.DecisionLog.

Nunca escribe nada de vuelta a Link -- ver README.md, gobernanza. Ese es el
alcance de Fase 2, condicionado a aprobacion formal del negocio.

Uso:
    python manage.py run_allocator --once --limit 5      # prueba rapida
    python manage.py run_allocator --hours 1 --limit 50
"""

import logging
import time
from datetime import datetime, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import DatabaseError, transaction
from django.utils import timezone

from audit.models import DecisionLog
from intake.link_client import (
    LinkInternalApiError,
    get_allocations_for_survey,
    get_awarded_survey_suppliers,
    get_survey_supplier_detail,
)
from intake.models import AwardedSurvey
from orchestrator.models import AllocatorRuntimeState
from rules_engine.engine import SurveyEvaluationInput, evaluate_survey

logger = logging.getLogger(__name__)


def _parse_link_datetime(value):
    """Link devuelve fechas tipo '2026-08-06 08:58:16 -0700'. django.utils.timezone
    espera algo timezone-aware; %z si soporta el formato '-0700' (sin ':')."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


class Command(BaseCommand):
    help = "Sondea encuestas AWARDED de Link y registra decisiones de rules_engine. Nunca escribe a Link."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=float, default=1.0, help="Duracion total en horas (default 1)")
        parser.add_argument("--limit", type=int, default=50, help="Encuestas por pasada (default 50)")
        parser.add_argument("--delay", type=float, default=1.0, help="Segundos entre encuestas (default 1)")
        parser.add_argument("--poll-interval", type=float, default=30.0, help="Segundos de espera si no hay nada nuevo")
        parser.add_argument("--once", action="store_true", help="Una sola pasada y termina (sin reintentos)")

    def handle(self, *args, **options):
        token = settings.LINK_API_TOKEN
        if not token:
            self._update_runtime_state(
                process_status=AllocatorRuntimeState.ProcessStatus.ERROR,
                token_status=AllocatorRuntimeState.TokenStatus.MISSING,
                heartbeat_at=timezone.now(),
            )
            self.stderr.write(self.style.ERROR("Falta LINK_API_TOKEN en .env -- no puedo llamar a la API real de Link."))
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
        self.stdout.write(self.style.SUCCESS(
            f"Allocator iniciado -- hasta {deadline:%Y-%m-%d %H:%M:%S} ({hours}h), limite/pasada={limit}"
        ))

        total_evaluated = 0
        while True:
            poll_at = timezone.now()
            self._update_runtime_state(heartbeat_at=poll_at, last_poll_attempt_at=poll_at)

            try:
                awarded_rows = get_awarded_survey_suppliers(token=token, limit=limit)
            except LinkInternalApiError as e:
                token_status = (
                    AllocatorRuntimeState.TokenStatus.INVALID
                    if e.status_code in {401, 403}
                    else AllocatorRuntimeState.TokenStatus.UNKNOWN
                )
                self._update_runtime_state(
                    heartbeat_at=timezone.now(),
                    last_poll_error=f"Link API HTTP {e.status_code}",
                    token_status=token_status,
                )
                self.stderr.write(self.style.WARNING(f"Link API error: {e}"))
                if once:
                    break
                time.sleep(poll_interval)
                if timezone.now() >= deadline:
                    break
                continue

            poll_success_at = timezone.now()
            self._update_runtime_state(
                heartbeat_at=poll_success_at,
                last_poll_success_at=poll_success_at,
                last_poll_error="",
                last_surveys_seen_count=len(awarded_rows),
                token_status=AllocatorRuntimeState.TokenStatus.VALID,
            )
            self.stdout.write(f"AWARDED encontradas: {len(awarded_rows)}")

            evaluated_this_pass = 0
            for row in awarded_rows[:limit]:
                if timezone.now() >= deadline and not once:
                    break
                try:
                    self._evaluate_one(row["id"], token)
                    evaluated_this_pass += 1
                    total_evaluated += 1
                except Exception:
                    # Una encuesta con error no debe frenar las demas.
                    logger.exception(
                        "Survey evaluation failed",
                        extra={"event": "allocator_evaluation_failed", "survey_supplier_id": row.get("id")},
                    )
                    self.stderr.write(self.style.WARNING(f"Error evaluando {row.get('id')}"))
                time.sleep(delay)

            self._update_runtime_state(
                heartbeat_at=timezone.now(),
                last_evaluated_count=evaluated_this_pass,
            )

            if once:
                break
            if timezone.now() >= deadline:
                break
            if evaluated_this_pass == 0:
                self.stdout.write(f"Sin encuestas nuevas -- esperando {poll_interval:.0f}s...")
                time.sleep(poll_interval)

        self._update_runtime_state(
            process_status=AllocatorRuntimeState.ProcessStatus.STOPPED,
            heartbeat_at=timezone.now(),
        )
        self.stdout.write(self.style.SUCCESS(f"Allocator terminado -- {total_evaluated} encuestas evaluadas."))

    def _evaluate_one(self, survey_supplier_id: str, token: str) -> None:
        detail = get_survey_supplier_detail(survey_supplier_id, token)

        try:
            allocations = get_allocations_for_survey(survey_supplier_id, settings.LINK_JWT_TOKEN)
        except LinkInternalApiError:
            # LINK_JWT_TOKEN vacio o vencido -- ver intake/link_client.py. No
            # es fatal: se evalua sin datos de allocations (el chequeo de
            # exclusion ya viene deshabilitado por defecto de todas formas).
            allocations = []

        buyer_name = (detail.get("project") or {}).get("client", {}).get("name", "")
        type_industry_name = (detail.get("type_industry") or {}).get("name", "")
        active_suppliers = [
            a.get("client_supplier__name", "") for a in allocations if a.get("active")
        ]
        survey_created_at = _parse_link_datetime(detail.get("date_created"))

        eval_input = SurveyEvaluationInput(
            survey_supplier_id=survey_supplier_id,
            buyer_name=buyer_name,
            type_industry_name=type_industry_name,
            cpi=detail.get("cpi") or 0.0,
            cpi_client=detail.get("cpi_client") or 0.0,
            active_allocation_supplier_names=active_suppliers,
        )
        decision = evaluate_survey(eval_input)

        # Ambos writes en una sola transaccion -- antes eran 2 commits
        # separados por encuesta (update_or_create + create), compitiendo por
        # el lock de sqlite contra run_earnings_tracker en simultaneo. Los
        # llamados de red (arriba) ya terminaron, asi que este bloque no
        # mantiene el lock abierto esperando I/O.
        with transaction.atomic():
            survey, _ = AwardedSurvey.objects.update_or_create(
                survey_supplier_id=survey_supplier_id,
                defaults={
                    "survey_name": detail.get("name", ""),
                    "survey_created_at": survey_created_at,
                    "project_id": (detail.get("project") or {}).get("id", ""),
                    "buyer_client_id": (detail.get("project") or {}).get("client", {}).get("id", ""),
                    "buyer_name": buyer_name,
                    "type_industry_name": type_industry_name,
                    "status_name": (detail.get("status") or {}).get("name", ""),
                    "cpi": detail.get("cpi"),
                    "cpi_client": detail.get("cpi_client"),
                    "ir": detail.get("ir"),
                    "loi": detail.get("loi"),
                    "noc": detail.get("noc"),
                    "raw_survey_supplier": detail,
                    "raw_allocations": allocations,
                },
            )

            DecisionLog.objects.create(
                survey=survey,
                buyer_name=buyer_name,
                type_industry_name=type_industry_name,
                verdict=decision.verdict,
                cpi=eval_input.cpi,
                cpi_client=eval_input.cpi_client,
                profit_floor_used=decision.profit_check.floor_used,
                computed_profit_percent=decision.profit_check.profit_percent,
                profit_floor_passed=decision.profit_check.passed,
                exclusion_rule_triggered=decision.exclusion_check.triggered_rule_id or "",
                exclusion_passed=decision.exclusion_check.passed,
                ranking_in_scope=decision.ranking_check.in_scope,
                cpi_suggestion_status=decision.cpi_suggestion.status,
                raw_input={
                    "survey_supplier_id": eval_input.survey_supplier_id,
                    "buyer_name": eval_input.buyer_name,
                    "type_industry_name": eval_input.type_industry_name,
                    "cpi": eval_input.cpi,
                    "cpi_client": eval_input.cpi_client,
                    "active_allocation_supplier_names": eval_input.active_allocation_supplier_names,
                },
                raw_decision={
                    "verdict": decision.verdict,
                    "profit_check": vars(decision.profit_check),
                    "exclusion_check": vars(decision.exclusion_check),
                    "ranking_check": vars(decision.ranking_check),
                    "cpi_suggestion": vars(decision.cpi_suggestion),
                },
            )
        self.stdout.write(f"{decision.verdict} -- {survey.survey_name or survey_supplier_id}")

    def _update_runtime_state(self, **fields) -> None:
        try:
            AllocatorRuntimeState.objects.update_or_create(name="main", defaults=fields)
        except DatabaseError:
            logger.exception("Could not persist allocator runtime state", extra={"event": "allocator_state_update_failed"})
