#!/usr/bin/env bash
set -Eeuo pipefail

# Arranca manualmente uno de los workers del allocator mediante la unidad
# instanciada eros-allocator-worker@<cmd>.service. Escribe los parametros de
# ejecucion en /run/eros-allocator/worker-<cmd>.env y luego inicia el servicio.
# Mismo rol que start-screening.sh en eros-prime-gate.
#
# Uso:
#   sudo bash deploy/scripts/start-worker.sh run_allocator --hours 24 --limit 50
#   sudo bash deploy/scripts/start-worker.sh run_earnings_tracker --hours 24
#
# El worker tiene Restart=always: al cumplirse --hours (o si el proceso cae)
# systemd lo reinicia con los mismos parametros. La unica forma de detenerlo es
#   sudo systemctl stop eros-allocator-worker@<cmd>
# o un nuevo deploy (deploy.sh detiene y deshabilita ambos workers).

RUN_DIR="/run/eros-allocator"
APP_GROUP="eros-allocator"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Ejecuta este script con sudo." >&2
    exit 1
fi

if [[ $# -lt 1 ]]; then
    echo "Uso: sudo bash deploy/scripts/start-worker.sh <run_allocator|run_earnings_tracker> [--hours N] [--limit N] [--delay N] [--poll-interval N]" >&2
    exit 2
fi

WORKER="$1"
shift

case "${WORKER}" in
    run_allocator)
        HOURS=24; LIMIT=50; DELAY=1; POLL_INTERVAL=30 ;;
    run_earnings_tracker)
        HOURS=24; LIMIT=200; DELAY=0.3; POLL_INTERVAL=300 ;;
    *)
        echo "Worker desconocido: ${WORKER} (usa run_allocator o run_earnings_tracker)." >&2
        exit 2 ;;
esac

while [[ $# -gt 0 ]]; do
    case "$1" in
        --hours)         HOURS="$2"; shift 2 ;;
        --limit)         LIMIT="$2"; shift 2 ;;
        --delay)         DELAY="$2"; shift 2 ;;
        --poll-interval) POLL_INTERVAL="$2"; shift 2 ;;
        *) echo "Argumento no reconocido: $1" >&2; exit 2 ;;
    esac
done

for name in HOURS LIMIT DELAY POLL_INTERVAL; do
    value="${!name}"
    if ! [[ "${value}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
        echo "${name} debe ser numerico (recibido: '${value}')." >&2
        exit 2
    fi
done

UNIT="eros-allocator-worker@${WORKER}"
if systemctl is-active --quiet "${UNIT}"; then
    echo "${UNIT} ya esta corriendo. Detenlo primero con: sudo systemctl stop ${UNIT}" >&2
    exit 1
fi

install -d -m 0750 -o root -g "${APP_GROUP}" "${RUN_DIR}"
ENV_OUT="${RUN_DIR}/worker-${WORKER}.env"
umask 027
cat > "${ENV_OUT}" <<EOF
WORKER_HOURS=${HOURS}
WORKER_LIMIT=${LIMIT}
WORKER_DELAY=${DELAY}
WORKER_POLL_INTERVAL=${POLL_INTERVAL}
EOF
chown root:"${APP_GROUP}" "${ENV_OUT}"
chmod 0640 "${ENV_OUT}"

systemctl daemon-reload
systemctl start "${UNIT}"

echo "${UNIT} iniciado con: --hours ${HOURS} --limit ${LIMIT} --delay ${DELAY} --poll-interval ${POLL_INTERVAL}"
echo "Seguir el log:   sudo journalctl -u ${UNIT} -f"
echo "Detener:         sudo systemctl stop ${UNIT}"
