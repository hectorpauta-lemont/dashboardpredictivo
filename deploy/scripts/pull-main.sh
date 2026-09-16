#!/usr/bin/env bash
set -Eeuo pipefail

# Actualiza la rama main sin interaccion, usando la deploy key de solo lectura
# guardada fuera del repositorio. Rechaza cambios locales y merges que no sean
# fast-forward. Mismo patron que eros-prime-gate.

APP_DIR="/opt/eros-allocator"
APP_USER="eros-allocator"
BRANCH="main"
REMOTE="origin"
EXPECTED_ORIGIN="git@github.com:thinknow-us/eros-allocator.git"
SSH_KEY="/var/lib/eros-allocator/github-deploy-key"
KNOWN_HOSTS="/var/lib/eros-allocator/known_hosts"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Ejecuta este script con sudo." >&2
    exit 1
fi

if [[ ! -d "${APP_DIR}/.git" ]]; then
    echo "No se encontro el repositorio Git en ${APP_DIR}." >&2
    exit 1
fi

if ! id "${APP_USER}" >/dev/null 2>&1; then
    echo "No existe el usuario ${APP_USER}; ejecuta primero bootstrap-ubuntu.sh." >&2
    exit 1
fi

if [[ ! -f "${SSH_KEY}" ]]; then
    echo "Falta la deploy key ${SSH_KEY}." >&2
    exit 1
fi

if [[ ! -f "${KNOWN_HOSTS}" ]]; then
    echo "Falta el archivo de hosts SSH ${KNOWN_HOSTS}." >&2
    exit 1
fi

if ! runuser -u "${APP_USER}" -- test -r "${SSH_KEY}"; then
    echo "${APP_USER} no puede leer la deploy key ${SSH_KEY}." >&2
    exit 1
fi

if ! runuser -u "${APP_USER}" -- test -r "${KNOWN_HOSTS}"; then
    echo "${APP_USER} no puede leer ${KNOWN_HOSTS}." >&2
    exit 1
fi

SSH_COMMAND="ssh -i ${SSH_KEY} -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=${KNOWN_HOSTS}"

run_git() {
    runuser -u "${APP_USER}" -- env \
        HOME="${APP_DIR}" \
        GIT_TERMINAL_PROMPT=0 \
        GIT_SSH_COMMAND="${SSH_COMMAND}" \
        git -C "${APP_DIR}" "$@"
}

origin_url="$(run_git remote get-url "${REMOTE}")"
if [[ "${origin_url}" != "${EXPECTED_ORIGIN}" ]]; then
    echo "El remoto ${REMOTE} no coincide con el repositorio esperado." >&2
    echo "Esperado: ${EXPECTED_ORIGIN}" >&2
    echo "Actual:   ${origin_url}" >&2
    exit 1
fi

if [[ -n "$(run_git status --porcelain)" ]]; then
    echo "El repositorio tiene cambios locales; no se descargaron cambios." >&2
    run_git status --short >&2
    exit 1
fi

if ! run_git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
    echo "No existe la rama local ${BRANCH}." >&2
    exit 1
fi

echo "Descargando ${REMOTE}/${BRANCH}..."
run_git fetch --prune "${REMOTE}" \
    "refs/heads/${BRANCH}:refs/remotes/${REMOTE}/${BRANCH}"
run_git switch "${BRANCH}"
run_git merge --ff-only "${REMOTE}/${BRANCH}"

local_commit="$(run_git rev-parse "${BRANCH}")"
remote_commit="$(run_git rev-parse "${REMOTE}/${BRANCH}")"
if [[ "${local_commit}" != "${remote_commit}" ]]; then
    echo "La rama local no coincide con ${REMOTE}/${BRANCH}; revisa su historial." >&2
    exit 1
fi

echo "${BRANCH} actualizada en el commit ${local_commit}."
echo "Para desplegar ejecuta: sudo bash ${APP_DIR}/deploy/scripts/deploy.sh"
