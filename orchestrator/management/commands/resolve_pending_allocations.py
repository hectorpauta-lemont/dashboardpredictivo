"""
Vuelve a pedirle a Link las allocations de encuestas donde, al momento del
poll, `raw_allocations` vino vacio -- confirmado con el usuario (2026-09-21,
revisando encuestas reales en Link) que SI tienen proveedor asignado, Link
simplemente no lo habia terminado de resolver todavia cuando lo leimos. No es
"sin proveedor para siempre" -- por eso este comando corre APARTE, cada pocos
dias (no en cada poll en vivo), y corrige el snapshot guardado si Link ya
tiene la respuesta.

Acota la revision a los ultimos --days (default 30, el mismo horizonte que
el dashboard muestra por defecto) -- no tiene sentido reintentar para siempre
una encuesta cerrada hace meses que nunca se resolvio.

Uso:
    python manage.py resolve_pending_allocations              # ultimos 30 dias
    python manage.py resolve_pending_allocations --days 10 --limit 200
"""
from datetime import timedelta

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from intake.link_client import LinkInternalApiError, get_allocations_for_survey
from intake.models import LiveSurveySnapshot


class Command(BaseCommand):
    help = "Reintenta las allocations que vinieron vacias de Link y corrige el dato si ya se resolvio. Nunca escribe a Link."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30,
                            help="Cuantos dias hacia atras revisar (default 30).")
        parser.add_argument("--limit", type=int, default=500,
                            help="Maximo de snapshots pendientes a reintentar en esta pasada (default 500).")

    def handle(self, *args, **opts):
        jwt = settings.LINK_JWT_TOKEN
        if not jwt:
            self.stdout.write(self.style.WARNING("LINK_JWT_TOKEN vacio -- nada que resolver, saliendo."))
            return

        since = timezone.now() - timedelta(days=opts["days"])

        # Ultimo snapshot de cada (encuesta, dia) dentro de la ventana que
        # sigue con raw_allocations vacio -- eso es lo que alimenta el
        # "Pendiente de resolver" del rollup (build_dashboard_rollups usa el
        # ultimo snapshot del dia por encuesta, mismo criterio aca).
        latest_pk_by_survey_day: dict[tuple, int] = {}
        for sid, day, pk in (
            LiveSurveySnapshot.objects
            .filter(polled_at__gte=since, raw_allocations=[])
            .order_by("polled_at")
            .values_list("survey_supplier_id", "polled_at__date", "pk")
            .iterator(chunk_size=8000)
        ):
            latest_pk_by_survey_day[(sid, day)] = pk  # se queda con el ultimo (orden asc)

        pks = list(latest_pk_by_survey_day.values())[: opts["limit"]]
        candidates = list(
            LiveSurveySnapshot.objects.filter(pk__in=pks).only("id", "survey_supplier_id", "polled_at")
        )
        self.stdout.write(f"Reintentando {len(candidates)} snapshots pendientes (ultimos {opts['days']} dias)...")

        resolved = failed = still_empty = 0
        touched_days = set()
        for snap in candidates:
            try:
                allocations = get_allocations_for_survey(snap.survey_supplier_id, jwt)
            except LinkInternalApiError as e:
                failed += 1
                self.stdout.write(f"  FALLO {snap.survey_supplier_id}: {e.status_code}")
                continue
            if not allocations:
                still_empty += 1
                continue
            snap.raw_allocations = allocations
            snap.save(update_fields=["raw_allocations"])
            resolved += 1
            touched_days.add(timezone.localtime(snap.polled_at).date())

        self.stdout.write(self.style.SUCCESS(
            f"OK -- {resolved} resueltas, {still_empty} siguen vacias en Link, {failed} fallidas."
        ))

        if touched_days:
            span = (max(touched_days) - min(touched_days)).days + 1
            self.stdout.write(f"Reconstruyendo rollup de {span} dia(s) afectados...")
            call_command("build_dashboard_rollups", days=span)
