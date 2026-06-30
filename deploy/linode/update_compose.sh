#!/usr/bin/env bash
# Pulls the latest deploy/linode/<ROLE>/docker-compose.yml from the deploy-config-${ENV_NAME} branch
# and restarts the role's stack if anything changed. Roles: localchain, miner.
# (The validator role uses the top-level installer/ scripts and envs/deployed/docker-compose.yml.)

set -euo pipefail

ROLE="${1:?Usage: update_compose.sh <localchain|miner> [ENV_NAME] [WORKING_DIRECTORY]}"
ENV_NAME="${2:-prod}"
WORKING_DIRECTORY="${3:-$HOME/refinery-${ROLE}/}"

case "${ROLE}" in
    localchain|miner) ;;
    *) echo "Unsupported role: ${ROLE} (expected localchain or miner)" >&2; exit 1 ;;
esac

mkdir -p "${WORKING_DIRECTORY}"
cd "${WORKING_DIRECTORY}"

GITHUB_URL="https://raw.githubusercontent.com/backend-developers-ltd/refinery/refs/heads"

TEMP_FILE="$(mktemp "${TMPDIR:-/tmp}/refinery_compose_update.XXXXXX.yml")"
trap 'rm -f "${TEMP_FILE}"' EXIT
curl -fsSL "${GITHUB_URL}/deploy-config-${ENV_NAME}/deploy/linode/${ROLE}/docker-compose.yml" > "${TEMP_FILE}"

LOCAL_FILE="${WORKING_DIRECTORY}/docker-compose.yml"

if [ ! -f "${LOCAL_FILE}" ]; then
    echo "Local docker-compose.yml does not exist. Creating it."
    cat "${TEMP_FILE}" > "${LOCAL_FILE}"
    UPDATED=true
else
    if diff -q "${TEMP_FILE}" "${LOCAL_FILE}" > /dev/null; then
        echo "No changes detected in docker-compose.yml"
        UPDATED=false
    else
        echo "Changes detected in docker-compose.yml. Updating..."
        cat "${TEMP_FILE}" > "${LOCAL_FILE}"
        UPDATED=true
    fi
fi

if [ "${UPDATED}" = true ]; then
    echo "Updating services..."

    if command -v docker &> /dev/null && docker compose version &> /dev/null; then
        docker compose up -d --remove-orphans
    elif command -v docker-compose &> /dev/null; then
        docker-compose up -d --remove-orphans
    else
        echo "Error: Neither docker compose nor docker-compose is available."
        exit 1
    fi

    echo "Services updated successfully."
fi

echo "Update process completed."
