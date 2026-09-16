from datetime import date as date_cls
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import Prefetch
from django.http import HttpResponse
from django.utils import timezone
from django.views import View
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import filters
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import DecisionLog
from dashboard.metrics import _day_range, build_agent_insights, build_dashboard_metrics
from dashboard.permissions import HasAPIKey
from dashboard.serializers import SurveyDecisionDetailSerializer, SurveyDecisionSummarySerializer
from intake.models import AwardedSurvey, LiveSurveySnapshot


def _supplier_ranking_from_allocations(surveys):
    """
    Agrega intake.AwardedSurvey.raw_allocations (guardado por orchestrator
    via get_allocations_for_survey, que requiere LINK_JWT_TOKEN -- ver
    intake/link_client.py) por client_supplier__name. Solo cuenta
    allocations con entrants > 0 (actividad real) -- sino se diluye con las
    miles de asignaciones reservadas que nunca se usaron. Devuelve [] si
    todavia no hay ningun dato real (JWT vacio/vencido o sin re-poll
    todavia), nunca un numero inventado.

    Ojo: esto es SOLO el breakdown por volumen/conversion -- no es el
    ranking de "negocio mas atractivo" (ese sigue sin criterio confirmado
    con Piyush, ver 'attractiveness_ranking' mas abajo).
    """
    stats: dict[str, dict] = {}
    for survey in surveys:
        for alloc in (survey.raw_allocations or []):
            name = alloc.get("client_supplier__name")
            entrants = (alloc.get("statistics") or {}).get("entrants") or 0
            if not name or entrants <= 0:
                continue
            row = stats.setdefault(
                name,
                {"supplier_name": name, "entrants": 0, "completes": 0, "cpi_values": [], "survey_ids": set()},
            )
            row["entrants"] += entrants
            row["completes"] += (alloc.get("statistics") or {}).get("completes") or 0
            avg_cpi = (alloc.get("statistics") or {}).get("avg_cpi")
            if avg_cpi:
                row["cpi_values"].append(avg_cpi)
            row["survey_ids"].add(survey.survey_supplier_id)

    ranking = []
    for row in stats.values():
        ranking.append(
            {
                "supplier_name": row["supplier_name"],
                "survey_count": len(row["survey_ids"]),
                "total_entrants": row["entrants"],
                "total_completes": row["completes"],
                "conversion_rate_pct": round(row["completes"] / row["entrants"] * 100, 1) if row["entrants"] else None,
                "avg_cpi": round(sum(row["cpi_values"]) / len(row["cpi_values"]), 2) if row["cpi_values"] else None,
            }
        )
    ranking.sort(key=lambda r: r["total_completes"], reverse=True)
    return ranking


@extend_schema_view(
    get=extend_schema(
        tags=["decisions"],
        summary="SurveyDecisionList",
        description="Lista de encuestas AWARDED evaluadas por rules_engine, con su ultima decision.",
        parameters=[
            OpenApiParameter(
                name="buyer_name",
                required=False,
                type=OpenApiTypes.STR,
                description="Filtra por buyer exacto (ej. 'Symmetric API').",
            ),
            OpenApiParameter(
                name="search",
                required=False,
                type=OpenApiTypes.STR,
                description="Busca en survey_supplier_id, survey_name y buyer_name.",
            ),
            OpenApiParameter(
                name="ordering",
                required=False,
                type=OpenApiTypes.STR,
                description='Ordena por last_polled_at o survey_created_at. Prefijo "-" para descendente.',
            ),
            OpenApiParameter(
                name="show_all",
                required=False,
                type=OpenApiTypes.BOOL,
                description=(
                    "Por defecto solo devuelve encuestas creadas en los ultimos 30 dias -- "
                    "confirmado con Piyush (2026-08-26) que asi es como el ve AWARDED en Link. "
                    "Sin esto, el backlog completo (incluye encuestas AWARDED abandonadas hace "
                    "casi 2 anios) se mezclaba con lo activo. Pasar show_all=true para el historico completo."
                ),
            ),
        ],
    )
)
class SurveyDecisionListView(ListAPIView):
    """
    GET /api/decisions/
    GET /api/decisions/?buyer_name=Symmetric+API
    GET /api/decisions/?show_all=true

    Solo lectura -- nunca escribe a Link. Cada request lee el estado actual
    de la base de datos, poblada por orchestrator/run_allocator.py.
    """

    permission_classes = [HasAPIKey]
    serializer_class = SurveyDecisionSummarySerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["survey_supplier_id", "survey_name", "buyer_name"]
    ordering_fields = ["last_polled_at", "survey_created_at"]
    ordering = ["-last_polled_at"]

    def get_queryset(self):
        qs = AwardedSurvey.objects.prefetch_related(
            Prefetch("decisions", queryset=DecisionLog.objects.order_by("-evaluated_at", "-id"))
        )
        buyer_name = self.request.query_params.get("buyer_name")
        if buyer_name:
            qs = qs.filter(buyer_name=buyer_name)
        show_all = self.request.query_params.get("show_all", "").lower() == "true"
        if not show_all:
            recent_since = (timezone.now() - timedelta(days=30)).date()
            qs = qs.filter(survey_created_at__gte=_day_range(recent_since, recent_since)[0])
        return qs


@extend_schema_view(
    get=extend_schema(
        tags=["decisions"],
        summary="SurveyDecisionRetrieve",
        description="Detalle de una encuesta: historial completo de decisiones de rules_engine.",
        parameters=[
            OpenApiParameter(
                name="survey_supplier_id",
                required=True,
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="ID real del SurveySupplier en Link.",
            ),
        ],
    )
)
class SurveyDecisionDetailView(RetrieveAPIView):
    """GET /api/decisions/<survey_supplier_id>/ -- todas las evaluaciones registradas."""

    permission_classes = [HasAPIKey]
    serializer_class = SurveyDecisionDetailSerializer
    lookup_field = "survey_supplier_id"
    queryset = AwardedSurvey.objects.prefetch_related(
        Prefetch("decisions", queryset=DecisionLog.objects.order_by("-evaluated_at", "-id"))
    )


@extend_schema(
    tags=["business-summary"],
    summary="BusinessSummary",
    description=(
        "KPIs financieros y de gestion para un rango de fechas -- pensado para que el "
        "equipo de Link consuma esto y arme su propia pestana de monitoreo de negocio "
        "(este microservicio nunca renderiza HTML para Link, solo expone datos). "
        "Equivalente/mejora del reporte manual 'Link 2.0' (Power BI), pero calculado "
        "en tiempo real sobre datos ya sondeados de la API real de Link -- nunca "
        "requiere actualizacion manual."
    ),
    parameters=[
        OpenApiParameter(
            name="date_from", required=False, type=OpenApiTypes.DATE,
            description="Default: primer dia del mes actual (UTC).",
        ),
        OpenApiParameter(
            name="date_to", required=False, type=OpenApiTypes.DATE,
            description="Default: hoy (UTC).",
        ),
    ],
    responses={200: OpenApiTypes.OBJECT},
)
class BusinessSummaryView(APIView):
    """
    GET /api/business-summary/?date_from=2026-08-01&date_to=2026-08-26

    Suma, dia por dia dentro del rango, (ultimo snapshot del dia - primero
    del dia) por encuesta -- mismo criterio que run_earnings_tracker usa
    para "ganado hoy", aplicado a cualquier rango. Dias sin ningun snapshot
    guardado no aportan al total (se listan en "days_without_data"); nunca
    se inventa un numero para un dia sin dato real.
    """

    permission_classes = [HasAPIKey]

    def get(self, request):
        today = timezone.localtime(timezone.now()).date()
        month_start = today.replace(day=1)

        date_from_str = request.query_params.get("date_from") or month_start.isoformat()
        date_to_str = request.query_params.get("date_to") or today.isoformat()
        date_from = date_cls.fromisoformat(date_from_str)
        date_to = date_cls.fromisoformat(date_to_str)

        _start, _end = _day_range(date_from, date_to)
        snapshots = LiveSurveySnapshot.objects.filter(
            polled_at__gte=_start, polled_at__lt=_end
        ).order_by("polled_at")

        by_survey_day: dict[tuple, list] = {}
        by_survey_all: dict[str, list] = {}
        polled_days: set = set()
        for snap in snapshots:
            day = timezone.localtime(snap.polled_at).date()
            polled_days.add(day)
            by_survey_day.setdefault((snap.survey_supplier_id, day), []).append(snap)
            by_survey_all.setdefault(snap.survey_supplier_id, []).append(snap)

        revenue = cost = profit = 0.0
        total_completes_new = 0
        by_day_agg: dict[date_cls, dict] = {}
        buyer_agg: dict[str, dict] = {}

        for (survey_supplier_id, day), snaps in by_survey_day.items():
            first, latest = snaps[0], snaps[-1]
            new_completes = max(latest.completes - first.completes, 0)
            new_terms = max(latest.terms_count - first.terms_count, 0)
            day_revenue = new_completes * (latest.cpi_client or 0)
            day_cost = new_completes * (latest.cpi or 0)
            day_profit = new_completes * (latest.profit_per_complete or 0)

            revenue += day_revenue
            cost += day_cost
            profit += day_profit
            total_completes_new += new_completes

            day_row = by_day_agg.setdefault(day, {"completes": 0, "terminations": 0, "revenue": 0.0})
            day_row["completes"] += new_completes
            day_row["terminations"] += new_terms
            day_row["revenue"] += day_revenue

            buyer_name = latest.buyer_name or "Sin buyer"
            buyer_row = buyer_agg.setdefault(buyer_name, {"completes": 0, "revenue": 0.0})
            buyer_row["completes"] += new_completes
            buyer_row["revenue"] += day_revenue

        # Promedios/indicadores de gestion -- sobre el ULTIMO snapshot de cada
        # encuesta dentro del rango (estado actual, no delta).
        latest_per_survey = [snaps[-1] for snaps in by_survey_all.values()]

        def avg(values):
            values = [v for v in values if v is not None]
            return round(sum(values) / len(values), 2) if values else None

        avg_ir = avg([s.ir for s in latest_per_survey])
        avg_conversion = avg([s.perc_conversion for s in latest_per_survey])
        avg_cpi_real = avg([s.cpi for s in latest_per_survey if s.cpi])

        total_entrants = sum(s.entrants_count for s in latest_per_survey)
        total_terms_all = sum(s.terms_count for s in latest_per_survey)
        termination_rate = round(total_terms_all / total_entrants * 100, 2) if total_entrants else None

        successful_count = sum(1 for s in latest_per_survey if s.quota and s.completes >= s.quota)

        # Alertas de desviacion: encuestas cuya conversion del ULTIMO snapshot
        # cae >=15 puntos por debajo de su propio promedio historico dentro
        # del rango -- deteccion simple, sin modelo de IA, consistente con la
        # filosofia del resto del proyecto (logica determinista, auditable).
        conversion_alerts = []
        for survey_supplier_id, snaps in by_survey_all.items():
            conversions = [s.perc_conversion for s in snaps if s.perc_conversion is not None]
            if len(conversions) < 2:
                continue
            historical_avg = sum(conversions[:-1]) / len(conversions[:-1])
            latest_conversion = conversions[-1]
            if historical_avg - latest_conversion >= 15:
                latest_snap = snaps[-1]
                conversion_alerts.append({
                    "survey_supplier_id": survey_supplier_id,
                    "survey_name": latest_snap.survey_name,
                    "buyer_name": latest_snap.buyer_name,
                    "historical_avg_conversion": round(historical_avg, 2),
                    "latest_conversion": round(latest_conversion, 2),
                    "drop_points": round(historical_avg - latest_conversion, 2),
                })
        conversion_alerts.sort(key=lambda a: a["drop_points"], reverse=True)

        # Ultimo veredicto POR ENCUESTA dentro del rango -- sin distinct(field),
        # que es DISTINCT ON de Postgres y no existe en SQLite (mismo criterio
        # que mock_ui/views.py ya documenta).
        latest_verdict_by_survey: dict[int, str] = {}
        for decision in DecisionLog.objects.filter(
            evaluated_at__gte=_start, evaluated_at__lt=_end
        ).order_by("evaluated_at"):
            latest_verdict_by_survey[decision.survey_id] = decision.verdict
        verdict_counts: dict[str, int] = {}
        for verdict in latest_verdict_by_survey.values():
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

        all_days_in_range = {date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)}
        days_without_data = sorted((all_days_in_range - polled_days))

        # AWARDED (raw_allocations en AwardedSurvey) suele venir en 0 -- todavia
        # no lanzo. La actividad real de suppliers esta en el ULTIMO snapshot
        # LIVE de cada encuesta dentro del rango (latest_per_survey, ya
        # calculado arriba), donde run_earnings_tracker guarda lo mismo.
        supplier_ranking = _supplier_ranking_from_allocations(
            list(AwardedSurvey.objects.exclude(raw_allocations=[])) + latest_per_survey
        )

        data = {
            "period": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
            "kpis": {
                "revenue": round(revenue, 2),
                "cost": round(cost, 2),
                "profit": round(profit, 2),
                "profit_margin_pct": round(profit / revenue * 100, 2) if revenue else None,
                "roas": round(revenue / cost, 2) if cost else None,
                "avg_cpi_real": avg_cpi_real,
                "total_awarded_recent_30d": AwardedSurvey.objects.filter(
                    survey_created_at__gte=_day_range(today - timedelta(days=30), today)[0]
                ).count(),
                "total_awarded_backlog": AwardedSurvey.objects.filter(
                    survey_created_at__lt=_day_range(today - timedelta(days=30), today)[0]
                ).count(),
                "total_live_surveys_in_period": len(by_survey_all),
                "total_completes_in_period": total_completes_new,
                "avg_conversion_rate_pct": avg_conversion,
                "avg_ir_pct": avg_ir,
                "termination_rate_pct": termination_rate,
                "successful_surveys_count": successful_count,
            },
            "breakdown_by_buyer": sorted(
                (
                    {"buyer_name": name, "completes": row["completes"], "revenue": round(row["revenue"], 2)}
                    for name, row in buyer_agg.items()
                ),
                key=lambda r: r["revenue"], reverse=True,
            ),
            "breakdown_by_rules_engine_verdict": verdict_counts,
            "daily_trend": [
                {
                    "day": day.isoformat(),
                    "completes": row["completes"],
                    "terminations": row["terminations"],
                    "revenue": round(row["revenue"], 2),
                }
                for day, row in sorted(by_day_agg.items())
            ],
            "conversion_alerts": conversion_alerts[:20],
            "days_without_data": [d.isoformat() for d in days_without_data],
            "breakdown_by_supplier": supplier_ranking[:15] if supplier_ranking else [],
            "pending": {
                "breakdown_by_supplier": None if supplier_ranking else (
                    "LINK_JWT_TOKEN vacio/vencido o todavia sin re-poll con acceso -- "
                    "ver intake/link_client.py. No es un dato sin calcular, es acceso pendiente."
                ),
                "attractiveness_ranking": "Sin criterio confirmado con Piyush todavia (margen vs salud de proveedor vs volumen).",
            },
        }
        return Response(data)


@extend_schema(
    tags=["dashboard"],
    summary="DashboardMetrics",
    description=(
        "Contrato unico que consume el dashboard embebible de Link 2.0 "
        "(single-file HTML). Devuelve totales, tabla resumen, KPIs financieros / "
        "de gestion / estadisticos, breakdowns por proveedor y estado, series "
        "diarias, tendencia de IR con su recta de regresion, y el listado de "
        "encuestas. Todo en tiempo real sobre datos ya sondeados de Link -- "
        "nunca requiere actualizacion manual. El front hace polling cada N seg."
    ),
    parameters=[
        OpenApiParameter(name="date_from", required=False, type=OpenApiTypes.DATE,
                         description="Default: primer dia del mes actual (UTC)."),
        OpenApiParameter(name="date_to", required=False, type=OpenApiTypes.DATE,
                         description="Default: hoy (UTC)."),
        OpenApiParameter(name="api", required=False, type=OpenApiTypes.STR,
                         description="Filtro reservado (open_lab | symmetric). Aun no segmenta."),
        OpenApiParameter(name="suppliers", required=False, type=OpenApiTypes.STR,
                         description="CSV de proveedores. Filtro reservado."),
    ],
    responses={200: OpenApiTypes.OBJECT},
)
class DashboardMetricsView(APIView):
    """GET /api/dashboard-metrics/  -- solo lectura."""

    permission_classes = [HasAPIKey]

    def get(self, request):
        return Response(build_dashboard_metrics(request.query_params))


@extend_schema(
    tags=["dashboard"],
    summary="AgentInsights",
    description=(
        "Recomendaciones de margen que el dashboard muestra en un popup. "
        "Version deterministica v1 (hipotesis H1/H3 de la memoria tecnica): "
        "por proveedor con volumen y conversion sanas, propone bajar el CPI 5% "
        "y proyecta la ganancia asumiendo que el fill se mantiene (marcado como "
        "supuesto a validar). El agente LangChain de la Fase 2 reemplaza esta "
        "logica devolviendo el mismo shape, con elasticidad real y explicacion "
        "en lenguaje natural. Nunca ejecuta el cambio: 'action' alimenta el "
        "circuito de aprobacion humana."
    ),
    parameters=[
        OpenApiParameter(name="lookback_days", required=False, type=OpenApiTypes.INT,
                         description="Ventana de historico a considerar. Default 14."),
    ],
    responses={200: OpenApiTypes.OBJECT},
)
class AgentInsightsView(APIView):
    """GET /api/agent-insights/  -- solo lectura, consultivo. Nunca escribe a Link."""

    permission_classes = [HasAPIKey]

    def get(self, request):
        try:
            lookback = int(request.query_params.get("lookback_days", 7))
        except (TypeError, ValueError):
            lookback = 7
        return Response({
            "generated_at": timezone.now().isoformat(),
            "engine": "deterministic_v1",
            "governance": "advisory only — never writes to Link; profit floor stays with the rules engine",
            "insights": build_agent_insights(lookback),
        })


class DashboardPageView(View):
    """
    GET /dashboard/ -- sirve el dashboard embebible (single-file HTML) desde el
    MISMO origen que la API, para no depender de CORS ni de abrir un archivo a
    mano. La API_KEY se inyecta aca del lado del servidor (settings.API_KEY),
    nunca queda escrita en un archivo que circula. Uso interno.
    """

    _PAGE = Path(__file__).resolve().parent / "page.html"

    def get(self, request):
        html = self._PAGE.read_text(encoding="utf-8")
        html = html.replace('const API_KEY         = "";',
                            f'const API_KEY = "{settings.API_KEY}";', 1)
        resp = HttpResponse(html, content_type="text/html; charset=utf-8")
        # Permite que el frontend de Link lo muestre en un <iframe> (pestana).
        # DJANGO_DASHBOARD_FRAME_ANCESTORS = "https://link.thinknow.link,..."
        ancestors = getattr(settings, "DASHBOARD_FRAME_ANCESTORS", "")
        if ancestors:
            resp["Content-Security-Policy"] = "frame-ancestors 'self' " + ancestors.replace(",", " ")
            resp.headers.pop("X-Frame-Options", None)
        return resp
