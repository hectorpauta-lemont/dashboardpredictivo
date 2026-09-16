import logging
import secrets

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from orchestrator.models import AllocatorRuntimeState

logger = logging.getLogger(__name__)

# name de AllocatorRuntimeState -> comando de manage.py que lo mantiene.
WORKERS = {
    "main": "run_allocator",
    "earnings": "run_earnings_tracker",
}


@require_GET
def live(request):
    """Liveness: confirma que Django/Gunicorn puede responder."""
    return JsonResponse({"status": "ok", "service": "eros-allocator-api"})


@require_GET
def ready(request):
    """Readiness: confirma que la API puede consultar la base de datos."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        logger.exception(
            "Database readiness check failed",
            extra={"event": "database_readiness_failed"},
        )
        return JsonResponse({"status": "unavailable", "database": "error"}, status=503)
    return JsonResponse({"status": "ok", "database": "reachable"})


def _authorized(request) -> bool:
    expected = settings.API_KEY
    provided = request.headers.get("X-API-Key", "")
    return bool(expected and provided) and secrets.compare_digest(expected, provided)


def _iso(value):
    return value.isoformat() if value else None


def _worker_report(state: AllocatorRuntimeState, now) -> dict:
    heartbeat_age = (
        max(0, int((now - state.heartbeat_at).total_seconds()))
        if state.heartbeat_at
        else None
    )
    reasons = []
    if state.process_status != AllocatorRuntimeState.ProcessStatus.RUNNING:
        reasons.append(f"process_{state.process_status}")
    if heartbeat_age is None or heartbeat_age > settings.WORKER_HEALTH_MAX_AGE_SECONDS:
        reasons.append("heartbeat_stale")
    if state.token_status in {
        AllocatorRuntimeState.TokenStatus.INVALID,
        AllocatorRuntimeState.TokenStatus.MISSING,
    }:
        reasons.append(f"token_{state.token_status}")
    return {
        "healthy": not reasons,
        "reasons": reasons,
        "process_status": state.process_status,
        "token_status": state.token_status,
        "process_started_at": _iso(state.process_started_at),
        "heartbeat_at": _iso(state.heartbeat_at),
        "heartbeat_age_seconds": heartbeat_age,
        "last_poll_attempt_at": _iso(state.last_poll_attempt_at),
        "last_poll_success_at": _iso(state.last_poll_success_at),
        "last_poll_error": state.last_poll_error,
        "last_surveys_seen_count": state.last_surveys_seen_count,
        "last_evaluated_count": state.last_evaluated_count,
        "last_evaluated_survey_supplier_id": state.last_evaluated_survey_supplier_id,
    }


@require_GET
def worker(request):
    """Estado persistente de los workers manuales (run_allocator y
    run_earnings_tracker). Protegido porque expone detalle operativo."""
    if not _authorized(request):
        return JsonResponse({"detail": "API key invalida o ausente."}, status=403)

    now = timezone.now()
    states = {s.name: s for s in AllocatorRuntimeState.objects.all()}
    workers = {}
    all_healthy = True
    for name, command in WORKERS.items():
        state = states.get(name)
        if state is None:
            workers[name] = {
                "command": command,
                "healthy": False,
                "reasons": ["never_started"],
            }
            all_healthy = False
            continue
        report = _worker_report(state, now)
        report["command"] = command
        workers[name] = report
        all_healthy = all_healthy and report["healthy"]

    payload = {"status": "ok" if all_healthy else "degraded", "workers": workers}
    return JsonResponse(payload, status=200 if all_healthy else 503)
