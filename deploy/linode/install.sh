#!/usr/bin/env bash
# Installer for a Refinery Linode deploy role: localchain, miner or multi-validator.
# Expects a ready .env in the working directory (copy deploy/linode/<ROLE>/.env.example there and edit it
# before running). Fetches update_compose.sh, runs it once, and installs a cron job that keeps the
# role's docker-compose.yml in sync with the deploy-config-${ENV_NAME} branch.
#
# The single-subnet validator is NOT handled here - it uses the top-level installer/install.sh.

set -euo pipefail

ROLE="${1:?Usage: install.sh <localchain|miner|multi-validator> [ENV_NAME] [WORKING_DIRECTORY]}"
ENV_NAME="${2:-prod}"
WORKING_DIRECTORY="${3:-$HOME/refinery-${ROLE}/}"

case "${ROLE}" in
    localchain|miner|multi-validator) ;;
    *) echo "Unsupported role: ${ROLE} (expected localchain, miner or multi-validator)" >&2; exit 1 ;;
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

# Replaces the user's crontab line tagged with $1 (or appends it) with the cron line $2.
install_cron_line() {
    local tag="$1"
    local cmd="$2"
    local existing_crontab filtered_crontab
    existing_crontab="$(crontab -l 2>/dev/null || true)"
    filtered_crontab="$(printf "%s\n" "${existing_crontab}" | grep -F -v "${tag}" || true)"
    if [ -n "${filtered_crontab}" ]; then
        { printf "%s\n" "${filtered_crontab}"; printf "%s\n" "${cmd}"; } | crontab -
    else
        printf "%s\n" "${cmd}" | crontab -
    fi
}

CRON_TAG="REFINERY_${ROLE^^}_UPDATE"
CRON_CMD="*/15 * * * * curl -fsSL ${UPDATE_URL_Q} -o ${UPDATE_SCRIPT_Q} && chmod +x ${UPDATE_SCRIPT_Q} && ${UPDATE_SCRIPT_Q} ${ROLE_Q} ${ENV_NAME_Q} ${WORKING_DIRECTORY_Q} # ${CRON_TAG}"
install_cron_line "${CRON_TAG}" "${CRON_CMD}"

if [ "${ROLE}" = "localchain" ]; then
    # RocksDB's info LOG (<volume>/chains/<chain>/db/full/LOG) is never rotated by Substrate, so it grows
    # for the container's whole lifetime. Truncate, once a week, any LOG whose ALLOCATED size (du, not
    # the apparent size: truncating in place leaves a sparse file RocksDB keeps appending to) exceeds
    # 256 MB. Compose names the volumes after the working directory: <dir>_chain_one, _two, _three,
    # _archive.
    PROJECT_NAME="$(basename "${WORKING_DIRECTORY}")"
    printf -v LOG_PATH_Q "%q" "/var/lib/docker/volumes/${PROJECT_NAME}_chain_*/_data/chains/*/db/full/LOG"
    LOG_CRON_TAG="REFINERY_LOCALCHAIN_ROCKSDB_LOG"
    LOG_CRON_CMD="0 4 * * 0 sudo find /var/lib/docker/volumes -path ${LOG_PATH_Q} -exec du -k {} + | awk '\$1 > 262144 {print \$2}' | xargs -r sudo truncate -s 0 # ${LOG_CRON_TAG}"
    install_cron_line "${LOG_CRON_TAG}" "${LOG_CRON_CMD}"
fi

echo "Cron job installed successfully. It will run every 15 minutes."
echo "Role: ${ROLE}"
echo "Environment: ${ENV_NAME}"
echo "Working directory: ${WORKING_DIRECTORY}"
