#!/usr/bin/env bash
set -Eeuo pipefail

# Preparacion inicial de una instancia Ubuntu (recomendado: 24.04 LTS) para
# EROS Allocator. Se ejecuta UNA sola vez por instancia, antes del primer
# deploy.sh y antes de Certbot. Mismo patron que eros-prime-gate, sin
# Playwright/Chromium (este servicio no traza redirecciones).

APP_DIR="/opt/eros-allocator"
APP_USER="eros-allocator"
ENV_DIR="/etc/eros-allocator"
ENV_FILE="${ENV_DIR}/eros.env"
SERVER_NAME="${SERVER_NAME:-_}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Ejecuta este script con sudo." >&2
    exit 1
fi

if [[ ! -f /etc/os-release ]] || ! grep -q '^ID=ubuntu$' /etc/os-release; then
    echo "Este bootstrap esta preparado para Ubuntu (recomendado: 24.04)." >&2
    exit 1
fi

if [[ "${REPO_DIR}" != "${APP_DIR}" ]]; then
    echo "Clona el repositorio en ${APP_DIR} antes de ejecutar el bootstrap." >&2
    echo "Ruta actual: ${REPO_DIR}" >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    certbot \
    curl \
    git \
    libpq-dev \
    nginx \
    python3 \
    python3-certbot-nginx \
    python3-pip \
    python3-venv \
    rsyslog

if ! id "${APP_USER}" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "${APP_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
fi

install -d -m 0755 -o "${APP_USER}" -g "${APP_USER}" "${APP_DIR}"
install -d -m 0750 -o root -g "${APP_USER}" "${ENV_DIR}"
install -d -m 0750 -o syslog -g adm /var/log/eros-allocator
touch /var/log/eros-allocator/api.log /var/log/eros-allocator/worker.log
chown syslog:adm /var/log/eros-allocator/*.log
chmod 0640 /var/log/eros-allocator/*.log
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

if [[ ! -f "${ENV_FILE}" ]]; then
    install -m 0640 -o root -g "${APP_USER}" "${APP_DIR}/.env.example" "${ENV_FILE}"
    echo "Se creo ${ENV_FILE}; debes completar sus secretos antes del primer deploy."
fi

if [[ ! -x "${APP_DIR}/venv/bin/python" ]]; then
    runuser -u "${APP_USER}" -- python3 -m venv "${APP_DIR}/venv"
fi

runuser -u "${APP_USER}" -- "${APP_DIR}/venv/bin/pip" install --upgrade pip
runuser -u "${APP_USER}" -- "${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

install -m 0644 "${APP_DIR}/deploy/systemd/eros-allocator-api.service" /etc/systemd/system/eros-allocator-api.service
install -m 0644 "${APP_DIR}/deploy/systemd/eros-allocator-worker@.service" /etc/systemd/system/eros-allocator-worker@.service
install -m 0644 "${APP_DIR}/deploy/rsyslog/30-eros-allocator.conf" /etc/rsyslog.d/30-eros-allocator.conf
install -m 0644 "${APP_DIR}/deploy/logrotate/eros-allocator" /etc/logrotate.d/eros-allocator

sed "s/__SERVER_NAME__/${SERVER_NAME}/g" \
    "${APP_DIR}/deploy/nginx/eros-allocator.conf" \
    > /etc/nginx/sites-available/eros-allocator.conf
ln -sfn /etc/nginx/sites-available/eros-allocator.conf /etc/nginx/sites-enabled/eros-allocator.conf
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable nginx rsyslog eros-allocator-api
systemctl disable --now 'eros-allocator-worker@run_allocator' 'eros-allocator-worker@run_earnings_tracker' 2>/dev/null || true
systemctl restart nginx rsyslog

echo
echo "Bootstrap completado. Siguientes pasos:"
echo "  1. Edita ${ENV_FILE} y coloca los valores reales."
echo "  2. Ejecuta: sudo bash ${APP_DIR}/deploy/scripts/deploy.sh"
if [[ "${SERVER_NAME}" != "_" ]]; then
    echo "  3. Cuando el DNS apunte a esta EC2, activa HTTPS:"
    echo "     sudo certbot --nginx -d ${SERVER_NAME}"
else
    echo "  3. Configura SERVER_NAME y HTTPS; consulta deploy/README.md."
fi
