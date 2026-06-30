#!/usr/bin/env bash
# Installer for a Refinery Linode deploy role: localchain or miner.
# Expects a ready .env in the working directory (copy deploy/linode/<ROLE>/.env.example there and edit it
# before running). Fetches update_compose.sh, runs it once, and installs a cron job that keeps the
# role's docker-compose.yml in sync with the deploy-config-${ENV_NAME} branch.
#
# The validator role is NOT handled here - it uses the top-level installer/install.sh.

set -euo pipefail

ROLE="${1:?Usage: install.sh <localchain|miner> [ENV_NAME] [WORKING_DIRECTORY]}"
ENV_NAME="${2:-prod}"
WORKING_DIRECTORY="${3:-$HOME/refinery-${ROLE}/}"

case "${ROLE}" in
    localchain|miner) ;;
    *) echo "Unsupported role: ${ROLE} (expected localchain or miner)" >&2; exit 1 ;;
esac

mkdir -p "${WORKING_DIRECTORY}"
WORKING_DIRECTORY=$(realpath "${WORKING_DIRECTORY}")

ENV_FILE="${WORKING_DIRECTORY}/.env"
if [ ! -f "${ENV_FILE}" ]; then
    echo "Error: ${ENV_FILE} not found." >&2
    echo "Copy deploy/linode/${ROLE}/.env.example there, fill it in, then re-run the installer." >&2
    exit 1
fi

GITHUB_URL="https://raw.githubusercontent.com/backend-developers-ltd/refinery/refs/heads"
UPDATE_SCRIPT="${WORKING_DIRECTORY}/update_compose.sh"
UPDATE_URL="${GITHUB_URL}/deploy-config-${ENV_NAME}/deploy/linode/update_compose.sh"

echo "Running update_compose.sh once to ensure it works..."
curl -fsSL "${UPDATE_URL}" -o "${UPDATE_SCRIPT}"
chmod +x "${UPDATE_SCRIPT}"
if ! "${UPDATE_SCRIPT}" "${ROLE}" "${ENV_NAME}" "${WORKING_DIRECTORY}"; then
    echo "Error: update_compose.sh failed. Not adding cronline."
    exit 1
fi
echo "update_compose.sh ran successfully."

printf -v UPDATE_URL_Q "%q" "${UPDATE_URL}"
printf -v UPDATE_SCRIPT_Q "%q" "${UPDATE_SCRIPT}"
printf -v ROLE_Q "%q" "${ROLE}"
printf -v ENV_NAME_Q "%q" "${ENV_NAME}"
printf -v WORKING_DIRECTORY_Q "%q" "${WORKING_DIRECTORY}"

CRON_TAG="REFINERY_${ROLE^^}_UPDATE"
CRON_CMD="*/15 * * * * curl -fsSL ${UPDATE_URL_Q} -o ${UPDATE_SCRIPT_Q} && chmod +x ${UPDATE_SCRIPT_Q} && ${UPDATE_SCRIPT_Q} ${ROLE_Q} ${ENV_NAME_Q} ${WORKING_DIRECTORY_Q} # ${CRON_TAG}"

EXISTING_CRONTAB="$(crontab -l 2>/dev/null || true)"
FILTERED_CRONTAB="$(printf "%s\n" "${EXISTING_CRONTAB}" | grep -F -v "${CRON_TAG}" || true)"
if [ -n "${FILTERED_CRONTAB}" ]; then
    { printf "%s\n" "${FILTERED_CRONTAB}"; printf "%s\n" "${CRON_CMD}"; } | crontab -
else
    printf "%s\n" "${CRON_CMD}" | crontab -
fi

echo "Cron job installed successfully. It will run every 15 minutes."
echo "Role: ${ROLE}"
echo "Environment: ${ENV_NAME}"
echo "Working directory: ${WORKING_DIRECTORY}"
