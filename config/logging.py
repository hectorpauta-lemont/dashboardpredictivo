import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Formato compacto para journald y los archivos locales rotados.

    Mismo patron que config/logging.py en eros-prime-gate: una linea JSON por
    registro, con los campos `extra=` relevantes de este proyecto si vienen
    presentes en el LogRecord.
    """

    extra_fields = (
        "event",
        "survey_supplier_id",
        "verdict",
        "status_code",
        "alarm_type",
        "worker",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in self.extra_fields:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)
