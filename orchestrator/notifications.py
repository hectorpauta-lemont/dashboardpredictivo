"""
Alertas best-effort cuando una encuesta LIVE cae en zona de riesgo (IR bajo o
terminacion alta). Mismo patron que screening/notifications.py en
eros-prime-gate -- nunca debe interrumpir el loop principal: si falla el
envio, se loguea y se sigue.

Reutiliza los MISMOS canales y destinatarios que eros-prime-gate (confirmado
por el usuario, 2026-08-19): AWS SES para email (rol IAM en produccion) y el
mismo bot/chat de Telegram. Ambos opcionales via .env -- si faltan las
variables, ese canal se omite en silencio.
"""

import logging

import requests
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def notify_performance_alarm(alarm, channels: set[str] | None = None) -> dict[str, str]:
    """alarm: orchestrator.models.PerformanceAlarm ya guardada (is_active=True).

    Telegram desactivado (pedido del usuario, 2026-09-16): las alarmas ahora
    se muestran como popup en el dashboard en vez de Telegram. Email se
    mantiene. Pasar channels={"email","telegram"} explicito si se quiere
    reactivar sin tocar este default.
    """
    channels = channels or {"email"}
    label = alarm.survey_name or alarm.survey_supplier_id

    if alarm.alarm_type == "ir_low":
        subject = f"📉 EROS Allocator: IR bajo -- {label}"
        metric_line_en = f"IR: {alarm.value_at_trigger}% (alarm threshold: {alarm.threshold}%, floor: 10%)"
        metric_line_es = f"IR: {alarm.value_at_trigger}% (umbral de alarma: {alarm.threshold}%, piso real: 10%)"
    else:
        subject = f"⚠️ EROS Allocator: Terminacion alta -- {label}"
        metric_line_en = f"Termination rate: {alarm.value_at_trigger}% (alarm threshold: {alarm.threshold}%)"
        metric_line_es = f"Tasa de terminacion: {alarm.value_at_trigger}% (umbral de alarma: {alarm.threshold}%)"

    body = (
        "📊 EROS Allocator -- performance alarm on a LIVE survey\n"
        "📊 Agente EROS Allocator -- alarma de performance en encuesta LIVE\n\n"
        f"Survey / Encuesta: {label}\n"
        f"Buyer / Cliente: {alarm.buyer_name}\n"
        f"{metric_line_en}\n"
        f"{metric_line_es}\n"
        f"Survey supplier id: {alarm.survey_supplier_id}\n"
    )
    results = {}
    if "email" in channels:
        results["email"] = _send_email(subject, body)
    if "telegram" in channels:
        results["telegram"] = _send_telegram(f"{subject}\n\n{body}")
    return results


def _send_email(subject: str, body: str) -> str:
    """Un send_mail() por destinatario -- si uno no esta verificado en SES
    sandbox, no debe bloquear el envio a los demas."""
    recipients = [r.strip() for r in settings.PERFORMANCE_ALERT_EMAILS.split(",") if r.strip()]
    if not settings.AWS_SES_REGION_NAME or not settings.DEFAULT_FROM_EMAIL or not recipients:
        return "skipped"

    sent_count = 0
    for recipient in recipients:
        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                fail_silently=False,
            )
            sent_count += 1
        except Exception:
            logger.exception(
                "Failed to send performance alarm email",
                extra={"event": "allocator_alert_email_failed", "recipient": recipient},
            )

    if sent_count == len(recipients):
        return "sent"
    if sent_count > 0:
        return "partial"
    return "failed"


def _send_telegram(text: str) -> str:
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return "skipped"
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=25,
        )
        response.raise_for_status()
        return "sent"
    except Exception:
        logger.exception("Failed to send performance alarm Telegram message", extra={"event": "allocator_alert_telegram_failed"})
        return "failed"
