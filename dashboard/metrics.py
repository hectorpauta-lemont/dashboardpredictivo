"""
Agregaciones para el dashboard embebible de Link 2.0.

Todo se calcula en el momento sobre datos ya sondeados de la API real de Link
(intake.LiveSurveySnapshot, intake.AwardedSurvey, audit.DecisionLog,
orchestrator.PerformanceAlarm). Nunca escribe a Link ni inventa un valor para
un dia sin dato real -- mismo criterio que dashboard/views.BusinessSummaryView.

Dos entradas publicas:
  build_dashboard_metrics(date_from, date_to, filters) -> dict (contrato /api/dashboard-metrics)
  build_agent_insights(lookback_days)                   -> list[dict] (contrato /api/agent-insights)
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime as datetime_cls
from datetime import time as time_cls
from datetime import timedelta

from django.utils import timezone

from audit.models import DecisionLog
from intake.models import AwardedSurvey, LiveSurveySnapshot
from orchestrator.models import DailyMetricRollup, PerformanceAlarm

# Dias recientes que SIEMPRE se escanean en vivo (el rollup lo llena un cron
# 1x/dia, asi que hoy/ayer pueden estar incompletos o sin rollup todavia).
LIVE_TAIL_DAYS = 2

# Pedido explicito de Piyush (2026-08-26): "Test client" (y variantes) queda
# fuera de TODA vista -- mismo criterio que mock_ui.views.exclude_test_buyers.
TEST_BUYER = "Test client"

# -- verdicts (audit.DecisionLog.Verdict) --------------------------------------
V_ELIGIBLE = "eligible_needs_cpi_input"
V_BLOCKED_FLOOR = "blocked_profit_floor"
V_BLOCKED_EXCL = "blocked_exclusion"
V_OUT_OF_SCOPE = "out_of_scope"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 2) if values else None


def _linear_fit(ys: list[float]):
    """Minimos cuadrados y = m*x + b sobre x = 0..n-1. Devuelve (fitted, slope,
    projected_change, direction). Mismo criterio que
    eros-allocator-mock-ui/mock_ui/charts.py. n < 2 -> None."""
    n = len(ys)
    if n < 2:
        return None
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    intercept = my - slope * mx
    fitted = [round(slope * x + intercept, 2) for x in xs]
    projected_change = round(slope * (n - 1), 1)
    direction = "up" if slope > 0.3 else "down" if slope < -0.3 else "flat"
    return fitted, round(slope, 2), projected_change, direction


def _day_range(date_from: date_cls, date_to: date_cls):
    """Limites [date_from, date_to] como datetime aware, medio-abiertos ([inicio, fin)).
    Filtrar por estos en vez de campo__date__gte/lte deja que la consulta use el
    indice de la columna datetime -- el filtro __date forzaba un CAST por fila
    (django_datetime_cast_date en el SQL) que ignoraba el indice y escaneaba la
    tabla entera sin importar el rango pedido (medido: ~2s fijos por dashboard-metrics,
    igual con 1 dia que con 90)."""
    start = timezone.make_aware(datetime_cls.combine(date_from, time_cls.min))
    end = timezone.make_aware(datetime_cls.combine(date_to + timedelta(days=1), time_cls.min))
    return start, end


def _iso_or_none(value):
    try:
        return date_cls.fromisoformat(str(value).strip())
    except (ValueError, TypeError):
        return None


def _period_bounds(request_params):
    """Prioridad: date_from/date_to explicitos (ISO) > range (7|15|30|90 dias,
    o 'mtd' mes-a-la-fecha, o 'Nm' N meses). Default: ultimos 30 dias -- mismo
    criterio confirmado con Piyush para el panel."""
    today = timezone.localtime(timezone.now()).date()

    df = _iso_or_none(request_params.get("date_from"))
    dt = _iso_or_none(request_params.get("date_to"))
    if df or dt:
        date_from = df or (today - timedelta(days=30))
        date_to = dt or today
    else:
        rng = str(request_params.get("range", "30")).strip().lower()
        if rng == "mtd":
            date_from, date_to = today.replace(day=1), today
        elif rng.endswith("m") and rng[:-1].isdigit():
            date_from, date_to = today - timedelta(days=int(rng[:-1]) * 30), today
        elif rng.isdigit():
            date_from, date_to = today - timedelta(days=max(1, min(int(rng), 400))), today
        else:
            date_from, date_to = today - timedelta(days=30), today
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    return date_from, date_to


# Solo los campos que la aritmetica de deltas necesita -- SIN raw_allocations
# ni strings (eso pesa; se re-consulta aparte solo para el ultimo snapshot
# de cada encuesta, ~200 filas en vez de miles).
_DELTA_FIELDS = (
    "pk", "survey_supplier_id", "polled_at", "buyer_name",
    "completes", "terms_count", "entrants_count", "prescreens_count",
    "cpi", "cpi_client", "profit_per_complete", "ir",
)
_LATEST_FIELDS = _DELTA_FIELDS + ("survey_name", "perc_conversion", "quota", "raw_allocations")


def _daily_deltas(date_from, date_to):
    """Recorre snapshots del rango y arma, dia por dia y por encuesta,
    (ultimo del dia - primero del dia). Devuelve estructuras reutilizadas por
    varias secciones del dashboard."""
    start_dt, end_dt = _day_range(date_from, date_to)
    snapshots = (
        LiveSurveySnapshot.objects
        .filter(polled_at__gte=start_dt, polled_at__lt=end_dt)
        .exclude(buyer_name__iexact=TEST_BUYER)
        .only(*_DELTA_FIELDS)
        .order_by("polled_at")
        .iterator(chunk_size=4000)
    )

    by_survey_day: dict[tuple, list] = {}
    by_survey_all: dict[str, list] = {}
    polled_days: set = set()
    latest_pk_by_survey: dict[str, int] = {}
    for snap in snapshots:
        day = timezone.localtime(snap.polled_at).date()
        polled_days.add(day)
        by_survey_day.setdefault((snap.survey_supplier_id, day), []).append(snap)
        by_survey_all.setdefault(snap.survey_supplier_id, []).append(snap)
        latest_pk_by_survey[snap.survey_supplier_id] = snap.pk  # scan ordenado asc

    revenue = cost = profit = 0.0
    completes_new = terms_new = entrants_new = prescreens_new = 0
    by_day: dict[date_cls, dict] = {}
    ir_by_buyer_day: dict[str, dict] = {}

    for (sid, day), snaps in by_survey_day.items():
        first, latest = snaps[0], snaps[-1]
        nc = max(latest.completes - first.completes, 0)
        nt = max(latest.terms_count - first.terms_count, 0)
        ne = max(latest.entrants_count - first.entrants_count, 0)
        npre = max(latest.prescreens_count - first.prescreens_count, 0)
        d_rev = nc * (latest.cpi_client or 0)
        d_cost = nc * (latest.cpi or 0)
        d_prof = nc * (latest.profit_per_complete or 0)
        revenue += d_rev
        cost += d_cost
        profit += d_prof
        completes_new += nc
        terms_new += nt
        entrants_new += ne
        prescreens_new += npre
        row = by_day.setdefault(day, {"completed": 0, "terminate": 0, "overquota": 0,
                                      "transactions": 0, "revenue": 0.0, "cost": 0.0, "ir_vals": []})
        row["completed"] += nc
        row["terminate"] += nt
        row["transactions"] += ne
        row["revenue"] += d_rev
        row["cost"] += d_cost
        if latest.ir is not None:
            row["ir_vals"].append(latest.ir)
            ir_by_buyer_day.setdefault(latest.buyer_name or "—", {}).setdefault(day, []).append(latest.ir)

    # Ultimo snapshot de cada encuesta con los campos "pesados" -- ~200 filas.
    latest_per_survey = list(
        LiveSurveySnapshot.objects
        .filter(pk__in=list(latest_pk_by_survey.values()))
        .only(*_LATEST_FIELDS)
    )
    return {
        "revenue": revenue, "cost": cost, "profit": profit,
        "completes_new": completes_new, "terms_new": terms_new,
        "entrants_new": entrants_new, "prescreens_new": prescreens_new,
        "by_day": by_day, "ir_by_buyer_day": ir_by_buyer_day,
        "latest_per_survey": latest_per_survey,
        "by_survey_all": by_survey_all, "polled_days": polled_days,
    }


def _supplier_breakdown(latest_per_survey, awarded_alloc=None):
    """Agrega raw_allocations (JWT) + snapshots LIVE por proveedor. Devuelve
    filas con entrants/completes/avg_cpi/conversion."""
    stats: dict[str, dict] = {}
    if awarded_alloc is None:
        awarded_alloc = list(
            AwardedSurvey.objects.exclude(raw_allocations=[])
            .values_list("raw_allocations", flat=True)
        )
    seeds = [{"raw_allocations": a} for a in awarded_alloc] + [
        {"raw_allocations": getattr(s, "raw_allocations", None)} for s in latest_per_survey
    ]
    for survey in seeds:
        for alloc in (survey.get("raw_allocations") or []):
            name = alloc.get("client_supplier__name")
            st = alloc.get("statistics") or {}
            entrants = st.get("entrants") or 0
            if not name or entrants <= 0:
                continue
            row = stats.setdefault(name, {"name": name, "entrants": 0, "completes": 0, "cpi": []})
            row["entrants"] += entrants
            row["completes"] += st.get("completes") or 0
            if st.get("avg_cpi"):
                row["cpi"].append(st["avg_cpi"])
    out = []
    for r in stats.values():
        out.append({
            "name": r["name"],
            "transactions": r["entrants"],
            "completes": r["completes"],
            "conversion_rate": round(r["completes"] / r["entrants"] * 100, 1) if r["entrants"] else None,
            "avg_cpi": round(sum(r["cpi"]) / len(r["cpi"]), 2) if r["cpi"] else None,
        })
    out.sort(key=lambda x: x["transactions"], reverse=True)
    return out


def _latest_decision_by_survey(since_dt):
    """Ultima decision por encuesta desde `since_dt` (datetime, comparacion
    index-friendly). Los KPIs de gestion son ESTADO ACTUAL: una encuesta
    evaluada hace semanas no cuenta -- por eso se mira una ventana corta y
    reciente, no todo el rango del dashboard."""
    latest: dict[int, dict] = {}
    for d in (
        DecisionLog.objects
        .filter(evaluated_at__gte=since_dt)
        .order_by("evaluated_at")
        .values("survey_id", "verdict", "profit_floor_passed", "exclusion_passed",
                "computed_profit_percent", "profit_floor_used")
        .iterator(chunk_size=5000)
    ):
        latest[d["survey_id"]] = d
    counts: dict[str, int] = {}
    for row in latest.values():
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    return counts, latest


def _financials(agg):
    revenue, cost, profit = agg["revenue"], agg["cost"], agg["profit"]
    return {
        "revenue": round(revenue, 2),
        "cost": round(cost, 2),
        "profit": round(profit, 2),
        "margin_pct": round(profit / revenue * 100, 1) if revenue else None,
        "conv_rate": _avg([s.perc_conversion for s in agg["latest_per_survey"]]),
    }


def _pct_delta(cur, prev):
    if prev in (None, 0) or cur is None:
        return None
    return round((cur - prev) / abs(prev) * 100, 1)


def _window_aggregate(date_from, date_to):
    """Igual shape que _daily_deltas, pero para ventanas largas: lee
    DailyMetricRollup (rapido) para el historico y escanea LiveSurveySnapshot
    en vivo solo los ultimos LIVE_TAIL_DAYS (fresco). Los <= LIVE_TAIL_DAYS
    van 100% en vivo."""
    today = timezone.localtime(timezone.now()).date()
    tail_start = max(date_from, today - timedelta(days=LIVE_TAIL_DAYS))

    live = _daily_deltas(tail_start, date_to)
    by_day = dict(live["by_day"])
    ir_by_buyer_day = {b: dict(d) for b, d in live["ir_by_buyer_day"].items()}
    revenue = live["revenue"]; cost = live["cost"]; profit = live["profit"]
    completes_new = live["completes_new"]; terms_new = live["terms_new"]
    entrants_new = live["entrants_new"]; prescreens_new = live["prescreens_new"]
    supplier_acc: dict[str, dict] = {}

    hist_to = min(date_to, tail_start - timedelta(days=1))
    if hist_to >= date_from:
        for r in DailyMetricRollup.objects.filter(
            day__gte=date_from, day__lte=hist_to
        ).exclude(buyer_name__iexact=TEST_BUYER):
            revenue += r.revenue; cost += r.cost; profit += r.profit
            completes_new += r.completed; terms_new += r.terminated
            entrants_new += r.entrants; prescreens_new += r.prescreens
            row = by_day.setdefault(r.day, {"completed": 0, "terminate": 0, "overquota": 0,
                                            "transactions": 0, "revenue": 0.0, "cost": 0.0, "ir_vals": []})
            row["completed"] += r.completed
            row["terminate"] += r.terminated
            row["transactions"] += r.entrants
            row["revenue"] += r.revenue
            row["cost"] += r.cost
            if r.ir_n:
                row["ir_vals"].append(r.ir_sum / r.ir_n)
                bd = ir_by_buyer_day.setdefault(r.buyer_name or "—", {})
                bd.setdefault(r.day, []).append(r.ir_sum / r.ir_n)
            sup = supplier_acc.setdefault(r.supplier_name or "—",
                                          {"name": r.supplier_name or "—", "entrants": 0, "completes": 0, "cpi": []})
            sup["entrants"] += r.entrants
            sup["completes"] += r.completed

    # supplier_rows: rollup historico + breakdown en vivo del tail (raw_allocations)
    live_sup = {r["name"]: r for r in _supplier_breakdown(live["latest_per_survey"])}
    for name, r in live_sup.items():
        acc = supplier_acc.setdefault(name, {"name": name, "entrants": 0, "completes": 0, "cpi": []})
        acc["entrants"] += r["transactions"]; acc["completes"] += r["completes"]
        if r["avg_cpi"]:
            acc["cpi"].append(r["avg_cpi"])
    supplier_rows = sorted(
        ({"name": a["name"], "transactions": a["entrants"], "completes": a["completes"],
          "conversion_rate": round(a["completes"] / a["entrants"] * 100, 1) if a["entrants"] else None,
          "avg_cpi": round(sum(a["cpi"]) / len(a["cpi"]), 2) if a["cpi"] else None}
         for a in supplier_acc.values()),
        key=lambda x: x["transactions"], reverse=True,
    )

    return {
        "revenue": revenue, "cost": cost, "profit": profit,
        "completes_new": completes_new, "terms_new": terms_new,
        "entrants_new": entrants_new, "prescreens_new": prescreens_new,
        "by_day": by_day, "ir_by_buyer_day": ir_by_buyer_day,
        "latest_per_survey": live["latest_per_survey"],
        "by_survey_all": live["by_survey_all"], "polled_days": set(by_day),
        "supplier_rows": supplier_rows,
    }


# ---------------------------------------------------------------------------
# entrada publica 1 -- /api/dashboard-metrics
# ---------------------------------------------------------------------------
def build_dashboard_metrics(request_params) -> dict:
    date_from, date_to = _period_bounds(request_params)
    span = (date_to - date_from).days + 1
    prev_from = date_from - timedelta(days=span)
    prev_to = date_from - timedelta(days=1)

    agg = _window_aggregate(date_from, date_to)
    lps = agg["latest_per_survey"]
    fin = _financials(agg)

    # Deltas vs. la ventana anterior de igual largo -- barato: sale del rollup.
    prev = DailyMetricRollup.objects.filter(day__gte=prev_from, day__lte=prev_to)
    prev_rev = sum(r.revenue for r in prev) if prev.exists() else None
    prev_cost = None
    if prev_rev is not None:
        prev_cost = sum(r.cost for r in DailyMetricRollup.objects.filter(day__gte=prev_from, day__lte=prev_to))
    prev_fin = {"revenue": prev_rev, "cost": prev_cost, "conv_rate": None}

    # ---- totales / summary table ----
    # Todo el bloque de VOLUMEN y PLATA es "actividad del periodo" (delta entre
    # el primer y el ultimo snapshot de cada dia), consistente con revenue/cost.
    # Las TASAS promedio (ir, conversion) y los CONTEOS (surveys, alarmas) son
    # estado actual -- no son magnitudes acumulables.
    total_transactions = agg["entrants_new"]
    total_completed = agg["completes_new"]
    total_terms = agg["terms_new"]
    total_prescreens = agg["prescreens_new"]
    totals = {
        "surveys": len(agg["by_survey_all"]),
        "transactions": total_transactions,
        "completed": total_completed,
        "conv_rate": round(total_completed / total_transactions * 100, 2) if total_transactions else None,
        "cost": fin["cost"],
        "revenue": fin["revenue"],
        "profit": fin["profit"],
        "margin_pct": fin["margin_pct"],
    }

    # summary_rows -- agrupado por (anio, mes) de los dias con dato
    rows: dict[tuple, dict] = {}
    for day, r in agg["by_day"].items():
        key = (day.year, day.month)
        row = rows.setdefault(key, {"year": day.year, "month": day.month, "completed": 0,
                                    "transactions": 0, "revenue": 0.0, "cost": 0.0, "surveys": set()})
        row["completed"] += r["completed"]
        row["transactions"] += r["transactions"]
        row["revenue"] += r["revenue"]
        row["cost"] += r["cost"]
    # surveys por mes: contamos encuestas con snapshot en ese mes
    for sid, snaps in agg["by_survey_all"].items():
        for snap in snaps:
            key = (timezone.localtime(snap.polled_at).year, timezone.localtime(snap.polled_at).month)
            if key in rows:
                rows[key]["surveys"].add(sid)
    summary_rows = []
    for r in sorted(rows.values(), key=lambda x: (x["year"], x["month"])):
        summary_rows.append({
            "year": r["year"], "month": r["month"],
            "surveys": len(r["surveys"]),
            "transactions": r["transactions"],
            "completed": r["completed"],
            "conv_rate": round(r["completed"] / r["transactions"] * 100, 2) if r["transactions"] else None,
            "cost": round(r["cost"], 2),
            "revenue": round(r["revenue"], 2),
        })

    # ---- KPIs de gestion (allocator) -- estado actual (ultimos 5 dias) ----
    today = timezone.localtime(timezone.now()).date()
    today_start, today_end = _day_range(today, today)
    verdict_counts, latest_decision = _latest_decision_by_survey(
        timezone.now() - timedelta(days=5)
    )
    # auto-safe: heuristica (umbral formal pendiente de Piyush) -- elegible +
    # paso el piso + sin conflicto de exclusion + margen holgado (>= piso + 10).
    auto_safe = sum(
        1 for d in latest_decision.values()
        if d["verdict"] == V_ELIGIBLE and d["profit_floor_passed"] and d["exclusion_passed"]
        and d["computed_profit_percent"] is not None and d["profit_floor_used"] is not None
        and d["computed_profit_percent"] >= d["profit_floor_used"] + 10
    )
    management = {
        "eligible": verdict_counts.get(V_ELIGIBLE, 0),
        "blocked_profit_floor": verdict_counts.get(V_BLOCKED_FLOOR, 0),
        "blocked_exclusion": verdict_counts.get(V_BLOCKED_EXCL, 0),
        "awaiting_cpi_input": verdict_counts.get(V_ELIGIBLE, 0),  # elegible == falta que Piyush ponga CPI
        "auto_safe_candidates": auto_safe,
        "decisions_today": DecisionLog.objects.filter(
            evaluated_at__gte=today_start, evaluated_at__lt=today_end
        ).count(),
    }

    # ---- KPIs estadisticos ----
    ir_days = sorted(agg["by_day"].items())
    ir_series = [(d.isoformat(), _avg(r["ir_vals"])) for d, r in ir_days]
    ir_series = [(d, v) for d, v in ir_series if v is not None]
    fit = _linear_fit([v for _, v in ir_series])
    ir_trend = [{"day": _fmt_day(d), "ir": v} for d, v in ir_series]
    ir_trend_fit = (
        [{"day": _fmt_day(d), "ir": fv} for (d, _), fv in zip(ir_series, fit[0])]
        if fit else []
    )
    termination_rate = round(total_terms / total_transactions * 100, 1) if total_transactions else None
    statistical = {
        "ir_current": ir_series[-1][1] if ir_series else _avg([s.ir for s in lps]),
        "ir_slope_per_day": fit[1] if fit else 0,
        "ir_projected_change": fit[2] if fit else 0,
        "ir_direction": fit[3] if fit else "flat",
        "alarms_low_ir": PerformanceAlarm.objects.filter(
            alarm_type=PerformanceAlarm.AlarmType.IR_LOW, is_active=True).exclude(buyer_name__iexact=TEST_BUYER).count(),
        "alarms_high_term": PerformanceAlarm.objects.filter(
            alarm_type=PerformanceAlarm.AlarmType.TERMINATION_HIGH, is_active=True).exclude(buyer_name__iexact=TEST_BUYER).count(),
        "termination_rate_pct": termination_rate,
    }

    # ---- donuts ----
    # agg["supplier_rows"] (calculado en _window_aggregate) ya esta acotado al
    # rango pedido -- NO usar _supplier_breakdown(lps) aca: por defecto esa
    # funcion suma TODO AwardedSurvey.raw_allocations sin importar la fecha
    # (pensado para "ultimo estado conocido", no para un reporte por periodo).
    supplier_rows = agg["supplier_rows"]
    sup_total = sum(r["transactions"] for r in supplier_rows) or 1
    by_supplier = [
        {"name": r["name"], "transactions": r["transactions"],
         "pct": round(r["transactions"] / sup_total * 100, 2)}
        for r in supplier_rows[:6]
    ]
    overquota = max(0, total_transactions - total_completed - total_terms - total_prescreens)
    st_total = (total_completed + total_terms + overquota) or 1
    by_status = [
        {"name": "Completed", "transactions": total_completed, "pct": round(total_completed / st_total * 100, 1)},
        {"name": "Terminated", "transactions": total_terms, "pct": round(total_terms / st_total * 100, 1)},
        {"name": "Overquota", "transactions": overquota, "pct": round(overquota / st_total * 100, 1)},
    ]

    # ---- charts por dia ----
    status_by_day = [
        {"day": _fmt_day(d.isoformat()), "completed": r["completed"],
         "overquota": 0, "terminate": r["terminate"]}
        for d, r in ir_days
    ]
    revenue_by_day = [
        {"day": _fmt_day(d.isoformat()), "revenue": round(r["revenue"], 2), "cost": round(r["cost"], 2)}
        for d, r in ir_days
    ]

    # ---- external survey name (tambien alimenta el drill-down por buyer) ----
    external = sorted(
        ({
            "name": s.survey_name or s.survey_supplier_id,
            "buyer": s.buyer_name or "—",
            "supplier": _first_supplier(s),
            "status": "Live",
            "ir": round(s.ir, 1) if s.ir is not None else None,
            "completed": s.completes,
            "quota": s.quota,
            "conv_rate": round(s.perc_conversion, 1) if s.perc_conversion is not None else None,
            "terminate_rate": (
                round(s.terms_count / s.entrants_count * 100, 1)
                if s.entrants_count else None
            ),
        } for s in lps),
        key=lambda r: r["completed"], reverse=True,
    )[:200]

    # ---- ranking de proveedores (feature del panel anterior) ----
    supplier_ranking = [
        {
            "supplier": r["name"],
            "surveys": None,  # se completa abajo si hay datos por encuesta
            "transactions": r["transactions"],
            "completes": r["completes"],
            "conv_rate": r["conversion_rate"],
            "avg_cpi": r["avg_cpi"],
        }
        for r in supplier_rows[:15]
    ]

    # ---- regresion de IR AGRUPADA POR BUYER + drill-down (feature del panel) ----
    buyers_by_volume = sorted(
        agg["ir_by_buyer_day"].items(),
        key=lambda kv: sum(len(v) for v in kv[1].values()), reverse=True,
    )[:6]
    ir_trend_by_buyer = []
    for buyer, days_map in buyers_by_volume:
        pts = [(d, _avg(v)) for d, v in sorted(days_map.items())]
        pts = [(d, v) for d, v in pts if v is not None]
        bfit = _linear_fit([v for _, v in pts])
        ir_trend_by_buyer.append({
            "buyer": buyer,
            "points": [{"day": _fmt_day(d.isoformat()), "ir": v} for d, v in pts],
            "ir_current": pts[-1][1] if pts else None,
            "slope_per_day": bfit[1] if bfit else 0,
            "direction": bfit[3] if bfit else "flat",
            "projected_change": bfit[2] if bfit else 0,
        })

    # ---- alarmas activas (IR bajo / terminacion alta) -- vistas del panel ----
    now = timezone.now()
    alarms = [
        {
            "survey_name": a.survey_name or a.survey_supplier_id,
            "buyer": a.buyer_name or "—",
            "type": "ir_low" if a.alarm_type == PerformanceAlarm.AlarmType.IR_LOW else "termination_high",
            "value": round(a.value_at_trigger, 1) if a.value_at_trigger is not None else None,
            "threshold": round(a.threshold, 1),
            "triggered_at": a.triggered_at.isoformat(),
            "age_hours": round((now - a.triggered_at).total_seconds() / 3600, 1),
        }
        for a in PerformanceAlarm.objects.filter(is_active=True)
        .exclude(buyer_name__iexact=TEST_BUYER).order_by("-triggered_at")[:60]
    ]

    last_update = (
        max((s.polled_at for s in lps), default=None)
        or timezone.now()
    )

    return {
        "last_update": last_update.isoformat(),
        "period": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        "totals": totals,
        "summary_rows": summary_rows,
        "kpis": {
            "financial": {
                **fin,
                "revenue_delta_pct": _pct_delta(fin["revenue"], prev_fin["revenue"]),
                "cost_delta_pct": _pct_delta(fin["cost"], prev_fin["cost"]),
                "conv_rate_delta_pct": _pct_delta(fin["conv_rate"], prev_fin["conv_rate"]),
            },
            "management": management,
            "statistical": statistical,
        },
        "by_supplier": by_supplier,
        "by_status": by_status,
        "status_by_day": status_by_day,
        "ir_trend": ir_trend,
        "ir_trend_fit": ir_trend_fit,
        "ir_trend_by_buyer": ir_trend_by_buyer,
        "supplier_ranking": supplier_ranking,
        "alarms": alarms,
        "revenue_by_day": revenue_by_day,
        "external_surveys": external,
        "notes": {
            "cost": "CPI unicamente -- el dato de 'fee' todavia no se trae de Link.",
            "auto_safe_candidates": "Heuristica; umbral 'auto-safe' formal pendiente con Piyush.",
            "by_supplier": None if by_supplier else "Sin raw_allocations (LINK_JWT_TOKEN vacio/vencido o sin re-poll).",
        },
    }


def _fmt_day(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{['','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][int(m)]} {d}"


def _first_supplier(snap) -> str:
    for alloc in (snap.raw_allocations or []):
        n = alloc.get("client_supplier__name")
        if n:
            return n
    return "—"


# ---------------------------------------------------------------------------
# entrada publica 2 -- /api/agent-insights  (motor deterministico v1)
# ---------------------------------------------------------------------------
def build_agent_insights(lookback_days: int = 7) -> list[dict]:
    """
    Version deterministica de las recomendaciones de margen (hipotesis H1/H3
    de la memoria tecnica). El agente LangChain de la Fase 2 reemplaza/enriquece
    esta logica con elasticidad historica real y explicacion en lenguaje
    natural, devolviendo EL MISMO shape.

    Regla v1: para cada proveedor con volumen real y conversion sana, propone
    bajar el CPI 5% y proyecta la ganancia asumiendo que el fill se mantiene
    (marcado explicitamente como supuesto a validar).
    """
    since = timezone.now() - timedelta(days=lookback_days)
    latest_pk: dict[str, int] = {}
    for sid, pk in (
        LiveSurveySnapshot.objects.filter(polled_at__gte=since).exclude(buyer_name__iexact=TEST_BUYER)
        .order_by("polled_at").values_list("survey_supplier_id", "pk").iterator(chunk_size=8000)
    ):
        latest_pk[sid] = pk
    latest = list(
        LiveSurveySnapshot.objects.filter(pk__in=list(latest_pk.values()))
        .only("survey_supplier_id", "raw_allocations")
    )

    rows = _supplier_breakdown(latest)
    insights = []
    for r in rows:
        if not r["avg_cpi"] or r["transactions"] < 200 or r["completes"] < 50:
            continue
        conv = r["conversion_rate"] or 0
        if conv < 30:
            continue  # no tocar el precio de un proveedor con conversion floja
        new_cpi = round(r["avg_cpi"] * 0.95, 2)
        gain = round(r["completes"] * (r["avg_cpi"] - new_cpi), 2)
        if gain < 50:
            continue
        insights.append({
            "id": f"cpi-{r['name'].lower().replace(' ', '-')}",
            "kind": "margin_opportunity",
            "title": f"{r['name']}: lower CPI 5% (${r['avg_cpi']} → ${new_cpi})",
            "evidence": (
                f"Last {lookback_days}d: {r['completes']:,} completes on "
                f"{r['transactions']:,} transactions ({conv}% conversion) at avg CPI ${r['avg_cpi']}."
            ),
            "recommendation": f"Reduce CPI paid to {r['name']} by 5%.",
            "projected_gain_usd": gain,
            "assumption": "Assumes fill rate holds at the lower CPI — validate against this supplier's historical avg_cpi vs conversion before applying.",
            "confidence": "to_validate",
            "source": "deterministic_v1",
            "action": {"label": f"Apply: CPI → ${new_cpi}", "type": "cpi_change",
                       "supplier": r["name"], "from_cpi": r["avg_cpi"], "to_cpi": new_cpi},
        })
    insights.sort(key=lambda x: x["projected_gain_usd"], reverse=True)
    return insights[:8]
