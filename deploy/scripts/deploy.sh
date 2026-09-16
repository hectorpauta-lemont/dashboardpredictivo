#!/usr/bin/env bash
set -Eeuo pipefail

# Despliega una version despues del bootstrap. Sirve para el primer deploy y
# para cada actualizacion posterior. Mismo patron que eros-prime-gate.

APP_DIR="/opt/eros-allocator"
APP_USER="eros-allocator"
ENV_FILE="/etc/eros-allocator/eros.env"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Ejecuta este script con sudo." >&2
    exit 1
fi

if [[ ! -f "${APP_DIR}/manage.py" ]]; then
    echo "No se encontro el proyecto en ${APP_DIR}." >&2
    exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Falta ${ENV_FILE}; ejecuta primero bootstrap-ubuntu.sh." >&2
    exit 1
fi

if grep -Eq '^(DJANGO_SECRET_KEY=change-this|DJANGO_DEBUG=True$|API_KEY=$|LINK_API_TOKEN=$)' "${ENV_FILE}"; then
    echo "Completa todos los valores de produccion en ${ENV_FILE} antes de desplegar." >&2
    exit 1
fi

# EnvironmentFile de systemd y este script comparten formato. Usa comillas en
# eros.env si un valor contiene caracteres especiales del shell.
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

HEALTH_HOST="${DJANGO_ALLOWED_HOSTS%%,*}"
if [[ -z "${HEALTH_HOST}" || "${HEALTH_HOST}" == "localhost" || "${HEALTH_HOST}" == "127.0.0.1" ]]; then
    echo "Configura DJANGO_ALLOWED_HOSTS con el dominio real en ${ENV_FILE}." >&2
    exit 1
fi

required_database_variables=(
    DB_ENGINE
    DB_PORT
    DB_HOSTNAME
    DB_NAME
    DB_USERNAME
    DB_PASSWORD
)
for variable_name in "${required_database_variables[@]}"; do
    if [[ -z "${!variable_name:-}" ]]; then
        echo "Falta ${variable_name} en ${ENV_FILE}." >&2
        exit 1
    fi
done
if [[ "${DB_HOSTNAME,,}" == "localhost" || "${DB_HOSTNAME}" == "127.0.0.1" || "${DB_HOSTNAME}" == "::1" ]]; then
    echo "DB_HOSTNAME debe ser el endpoint privado de PostgreSQL RDS." >&2
    exit 1
fi

chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

run_as_app() {
    runuser -u "${APP_USER}" -- env "$@"
}

run_as_app "${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
run_as_app "${APP_DIR}/venv/bin/python" "${APP_DIR}/manage.py" check
run_as_app "${APP_DIR}/venv/bin/python" "${APP_DIR}/manage.py" migrate --noinput
run_as_app "${APP_DIR}/venv/bin/python" "${APP_DIR}/manage.py" collectstatic --noinput

install -m 0644 "${APP_DIR}/deploy/systemd/eros-allocator-api.service" /etc/systemd/system/eros-allocator-api.service
install -m 0644 "${APP_DIR}/deploy/systemd/eros-allocator-worker@.service" /etc/systemd/system/eros-allocator-worker@.service
install -m 0644 "${APP_DIR}/deploy/rsyslog/30-eros-allocator.conf" /etc/rsyslog.d/30-eros-allocator.conf
install -m 0644 "${APP_DIR}/deploy/logrotate/eros-allocator" /etc/logrotate.d/eros-allocator
nginx -t
systemctl daemon-reload
# Los workers son deliberadamente manuales. Cada deploy corta cualquier corrida
# activa y evita que queden habilitados para el siguiente arranque.
systemctl disable --now 'eros-allocator-worker@run_allocator' 'eros-allocator-worker@run_earnings_tracker' 2>/dev/null || true
systemctl restart eros-allocator-api nginx rsyslog

for attempt in {1..9}; do
    if curl --fail --silent \
        --connect-timeout 2 \
        --header "Host: ${HEALTH_HOST}" \
        --header "X-Forwarded-Proto: https" \
        http://127.0.0.1:8000/health/ready/ >/dev/null 2>&1; then
        echo "Deploy completado; la API responde correctamente."
        echo "Los workers quedan detenidos. Para arrancarlos:"
        echo "  sudo bash ${APP_DIR}/deploy/scripts/start-worker.sh run_allocator --hours 24"
        echo "  sudo bash ${APP_DIR}/deploy/scripts/start-worker.sh run_earnings_tracker --hours 24"
        exit 0
    fi
    sleep 1
done

echo "Los servicios reiniciaron, pero la API no supero la comprobacion HTTP." >&2
curl --fail --silent --show-error \
    --connect-timeout 2 \
    --header "Host: ${HEALTH_HOST}" \
    --header "X-Forwarded-Proto: https" \
    http://127.0.0.1:8000/health/ready/ >&2 || true
systemctl --no-pager --full status eros-allocator-api >&2 || true
journalctl -u eros-allocator-api -n 50 --no-pager >&2 || true
exit 1
