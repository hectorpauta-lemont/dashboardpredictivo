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

from dashboard.metrics import _day_range
from intake.models import LiveSurveySnapshot
from orchestrator.models import DailyMetricRollup


def _supplier(snap) -> str:
    for alloc in (snap.raw_allocations or []):
        n = alloc.get("client_supplier__name")
        if n:
            return n
    return "—"


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
                key = (last.buyer_name or "—", _supplier(last))
                a = agg[key]
                a["surveys"].add(sid)
                a["entrants"] += max(last.entrants_count - first.entrants_count, 0)
                a["completed"] += nc
                a["terminated"] += max(last.terms_count - first.terms_count, 0)
                a["prescreens"] += max(last.prescreens_count - first.prescreens_count, 0)
                a["revenue"] += nc * (last.cpi_client or 0)
                a["cost"] += nc * (last.cpi or 0)
                a["profit"] += nc * (last.profit_per_complete or 0)
                if last.ir is not None:
                    a["ir_sum"] += last.ir
                    a["ir_n"] += 1

            with transaction.atomic():
                DailyMetricRollup.objects.filter(day=day).delete()
                DailyMetricRollup.objects.bulk_create([
                    DailyMetricRollup(
                        day=day, buyer_name=b, supplier_name=sup,
                        surveys=len(a["surveys"]), entrants=a["entrants"],
                        completed=a["completed"], terminated=a["terminated"],
                        prescreens=a["prescreens"], revenue=round(a["revenue"], 2),
                        cost=round(a["cost"], 2), profit=round(a["profit"], 2),
                        ir_sum=round(a["ir_sum"], 2), ir_n=a["ir_n"],
                    )
                    for (b, sup), a in agg.items()
                ])
            total_rows += len(agg)
            self.stdout.write(f"  {day}: {len(agg)} filas ({len(by_survey)} encuestas)")

        self.stdout.write(self.style.SUCCESS(f"OK -- {total_rows} filas de rollup."))
