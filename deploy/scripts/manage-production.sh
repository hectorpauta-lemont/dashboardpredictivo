#!/usr/bin/env bash
set -Eeuo pipefail

# Ejecuta comandos de manage.py con el usuario, entorno virtual y variables de
# produccion correctos. Ejemplos: migrate, createsuperuser, shell, dbshell.

APP_DIR="/opt/eros-allocator"
APP_USER="eros-allocator"
ENV_FILE="/etc/eros-allocator/eros.env"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Ejecuta este script con sudo." >&2
    exit 1
fi

if [[ $# -eq 0 ]]; then
    echo "Uso: sudo bash deploy/scripts/manage-production.sh <comando-manage.py> [argumentos]" >&2
    exit 2
fi

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Falta ${ENV_FILE}; ejecuta primero bootstrap-ubuntu.sh." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

cd "${APP_DIR}"
runuser -u "${APP_USER}" -- env \
    HOME="${APP_DIR}" \
    "${APP_DIR}/venv/bin/python" "${APP_DIR}/manage.py" "$@"
