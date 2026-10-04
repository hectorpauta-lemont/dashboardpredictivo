"""
Sincroniza raw_survey_partner (precio/duracion por quota de Link, ver
intake/link_client.get_survey_partner_detail) para encuestas que todavia no
lo tienen, o que lo tienen pero sin closed_at -- corre APARTE de run_allocator
y run_earnings_tracker a proposito (pedido del usuario, 2026-09-21): esos dos
loops en vivo ya tienen suficiente carga y dependen de que el proceso este
arriba en el momento exacto en que una encuesta pasa por AWARDED o LIVE. Este
comando puede reintentar mas tarde sin perder nada -- barre lo que ya esta
guardado en la base, no depende de "estar mirando" en el instante correcto.

Uso (mismo patron que build_dashboard_rollups):
    python manage.py sync_survey_partner_data                # AwardedSurvey + ultimo LiveSurveySnapshot por encuesta
    python manage.py sync_survey_partner_data --limit 50      # acotar una pasada (cron diario)
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from intake.link_client import (
    LinkInternalApiError,
    extract_survey_partner_id,
    get_survey_partner_detail,
)
from intake.models import AwardedSurvey, LiveSurveySnapshot


class Command(BaseCommand):
    help = "Rellena/actualiza raw_survey_partner para encuestas que aun no lo tienen o no cerraron. Nunca escribe a Link."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=300,
                            help="Maximo de encuestas a procesar en esta pasada (default 300).")

    def handle(self, *args, **opts):
        jwt = settings.LINK_JWT_TOKEN
        if not jwt:
            self.stdout.write(self.style.WARNING("LINK_JWT_TOKEN vacio -- nada que sincronizar, saliendo."))
            return

        limit = opts["limit"]
        fetched = skipped_closed = failed = 0

        # --- AwardedSurvey: las que todavia no tienen nada guardado ---
        pending_awarded = list(
            AwardedSurvey.objects.exclude(raw_survey_supplier={})
            .filter(raw_survey_partner={})[:limit]
        )
        for survey in pending_awarded:
            partner_id = extract_survey_partner_id(survey.raw_survey_supplier)
            if not partner_id:
                continue
            try:
                data = get_survey_partner_detail(partner_id, jwt)
            except LinkInternalApiError as e:
                failed += 1
                self.stdout.write(f"  FALLO awarded {survey.survey_supplier_id}: {e.status_code}")
                continue
            with transaction.atomic():
                survey.raw_survey_partner = data
                survey.save(update_fields=["raw_survey_partner"])
            fetched += 1

        # --- LiveSurveySnapshot: la ULTIMA fila de cada encuesta que aun no
        # cerro (mismo criterio de "no volver a pedir si ya cerro" que usa
        # run_earnings_tracker._survey_partner_if_needed) ---
        remaining = max(0, limit - fetched)
        if remaining:
            latest_pk_by_survey: dict[str, int] = {}
            for sid, pk in (
                LiveSurveySnapshot.objects.order_by("polled_at")
                .values_list("survey_supplier_id", "pk").iterator(chunk_size=8000)
            ):
                latest_pk_by_survey[sid] = pk  # se queda con el ultimo (orden asc)

            latest_snaps = (
                LiveSurveySnapshot.objects
                .filter(pk__in=list(latest_pk_by_survey.values()))
                .only("id", "survey_supplier_id", "raw_survey_partner")
            )
            for snap in latest_snaps:
                if fetched >= limit:
                    break
                if snap.raw_survey_partner.get("closed_at"):
                    skipped_closed += 1
                    continue
                # Necesita el detalle de surveysupplier fresco para sacar el
                # link -- no viaja guardado aparte en LiveSurveySnapshot.
                awarded = AwardedSurvey.objects.filter(survey_supplier_id=snap.survey_supplier_id).first()
                detail = awarded.raw_survey_supplier if awarded else {}
                partner_id = extract_survey_partner_id(detail)
                if not partner_id:
                    continue
                try:
                    data = get_survey_partner_detail(partner_id, jwt)
                except LinkInternalApiError as e:
                    failed += 1
                    self.stdout.write(f"  FALLO live {snap.survey_supplier_id}: {e.status_code}")
                    continue
                with transaction.atomic():
                    snap.raw_survey_partner = data
                    snap.save(update_fields=["raw_survey_partner"])
                fetched += 1

        self.stdout.write(self.style.SUCCESS(
            f"OK -- {fetched} sincronizadas, {skipped_closed} ya cerradas (sin re-pedir), {failed} fallidas."
        ))
