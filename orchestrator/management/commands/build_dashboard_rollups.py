"""
Llena orchestrator.DailyMetricRollup a partir de intake.LiveSurveySnapshot.

Pensado para correr por cron 1x/dia (idempotente: reconstruye cada dia del
rango). El dashboard lee el rollup para ventanas largas sin escanear miles de
snapshots por request.

    python manage.py build_dashboard_rollups              # ultimos 120 dias
    python manage.py build_dashboard_rollups --days 400   # backfill largo
    python manage.py build_dashboard_rollups --days 3     # solo lo reciente (cron diario)
"""

from collections import defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from dashboard.metrics import PENDING_SUPPLIER_LABEL, _day_range
from intake.models import LiveSurveySnapshot
from orchestrator.models import DailyMetricRollup


def _supplier_shares(raw_allocations) -> dict:
    """Fraccion de completes por proveedor segun raw_allocations (estadisticas
    ya acumuladas por Link). Cuando una encuesta reparte completes entre mas
    de un proveedor, se PRORRATEA -- no se le asigna todo al primero que
    aparece (bug corregido 2026-09-18, ver dashboard/metrics._supplier_fraction
    para el mismo criterio del lado del dashboard en vivo). Sin desglose:
    todo va a '—' (sin proveedor identificado), nunca se adivina."""
    total = 0
    per_supplier: dict[str, int] = {}
    for alloc in (raw_allocations or []):
        name = alloc.get("client_supplier__name")
        st = alloc.get("statistics") or {}
        c = st.get("completes") or 0
        if not name or c <= 0:
            continue
        per_supplier[name] = per_supplier.get(name, 0) + c
        total += c
    if total <= 0:
        return {PENDING_SUPPLIER_LABEL: 1.0}
    return {name: c / total for name, c in per_supplier.items()}


class Command(BaseCommand):
    help = "Reconstruye DailyMetricRollup desde LiveSurveySnapshot (idempotente)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=120,
                            help="Cuantos dias hacia atras reconstruir (default 120).")

    def handle(self, *args, **opts):
        today = timezone.localtime(timezone.now()).date()
        start = today - timedelta(days=max(1, opts["days"]))
        self.stdout.write(f"Rollups {start} .. {today}")

        total_rows = 0
        for offset in range((today - start).days + 1):
            day = start + timedelta(days=offset)
            day_start, day_end = _day_range(day, day)
            snaps = list(
                LiveSurveySnapshot.objects
                .filter(polled_at__gte=day_start, polled_at__lt=day_end)
                .only("survey_supplier_id", "buyer_name", "polled_at", "completes",
                      "terms_count", "entrants_count", "prescreens_count",
                      "cpi", "cpi_client", "profit_per_complete", "ir", "raw_allocations")
                .order_by("polled_at")
            )
            if not snaps:
                continue

            by_survey = defaultdict(list)
            for s in snaps:
                by_survey[s.survey_supplier_id].append(s)

            # (buyer, supplier) -> acumulado del dia
            agg = defaultdict(lambda: {
                "surveys": set(), "entrants": 0, "completed": 0, "terminated": 0,
                "prescreens": 0, "revenue": 0.0, "cost": 0.0, "profit": 0.0,
                "ir_sum": 0.0, "ir_n": 0,
            })
            for sid, group in by_survey.items():
                first, last = group[0], group[-1]
                nc = max(last.completes - first.completes, 0)
                ne = max(last.entrants_count - first.entrants_count, 0)
                nt = max(last.terms_count - first.terms_count, 0)
                npre = max(last.prescreens_count - first.prescreens_count, 0)
                buyer = last.buyer_name or "—"
                shares = _supplier_shares(last.raw_allocations)
                # IR es una tasa, no un volumen -- no se prorratea. Se guarda
                # una sola vez por encuesta/dia (en el primer proveedor del
                # reparto) para no contarla dos veces si la encuesta se divide
                # entre varios proveedores ese dia.
                ir_assigned = False
                for sup, frac in shares.items():
                    a = agg[(buyer, sup)]
                    a["surveys"].add(sid)
                    a["entrants"] += ne * frac
                    a["completed"] += nc * frac
                    a["terminated"] += nt * frac
                    a["prescreens"] += npre * frac
                    a["revenue"] += nc * frac * (last.cpi_client or 0)
                    a["cost"] += nc * frac * (last.cpi or 0)
                    a["profit"] += nc * frac * (last.profit_per_complete or 0)
                    if last.ir is not None and not ir_assigned:
                        a["ir_sum"] += last.ir
                        a["ir_n"] += 1
                        ir_assigned = True

            with transaction.atomic():
                DailyMetricRollup.objects.filter(day=day).delete()
                DailyMetricRollup.objects.bulk_create([
                    DailyMetricRollup(
                        # entrants/completed/terminated/prescreens son enteros en el
                        # modelo -- se redondean solo aca (al guardar), nunca antes,
                        # para no acumular error de redondeo entre proveedores.
                        day=day, buyer_name=b, supplier_name=sup,
                        surveys=len(a["surveys"]), entrants=round(a["entrants"]),
                        completed=round(a["completed"]), terminated=round(a["terminated"]),
                        prescreens=round(a["prescreens"]), revenue=round(a["revenue"], 2),
                        cost=round(a["cost"], 2), profit=round(a["profit"], 2),
                        ir_sum=round(a["ir_sum"], 2), ir_n=a["ir_n"],
                    )
                    for (b, sup), a in agg.items()
                ])
            total_rows += len(agg)
            self.stdout.write(f"  {day}: {len(agg)} filas ({len(by_survey)} encuestas)")

        self.stdout.write(self.style.SUCCESS(f"OK -- {total_rows} filas de rollup."))
