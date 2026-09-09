#!/bin/bash
# Nightly observation polling runner.
# Intended for cron at 01:00; runs yesterday -> today as a one-day window.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"
VENV_ACTIVATE="${VENV_DIR}/bin/activate"
LOG_DIR="${SCRIPT_DIR}/logs"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="${LOG_DIR}/nightly_obs_poll_${TIMESTAMP}.log"

mkdir -p "${LOG_DIR}"

START_DATE=$(date -d "yesterday" +"%Y-%m-%d")
END_DATE=$(date +"%Y-%m-%d")
START_DT="${START_DATE} 00:00:00"
END_DT="${END_DATE} 00:00:00"

echo "=== Nightly Observation Poll ===" | tee -a "${LOG_FILE}"
echo "Timestamp: $(date)" | tee -a "${LOG_FILE}"
echo "Polling date range: ${START_DT} to ${END_DT}" | tee -a "${LOG_FILE}"
echo "Log file: ${LOG_FILE}" | tee -a "${LOG_FILE}"
echo "" | tee -a "${LOG_FILE}"

cd "${REPO_ROOT}"
if [[ ! -f "${VENV_ACTIVATE}" ]]; then
  echo "Virtual environment activation script not found: ${VENV_ACTIVATE}" | tee -a "${LOG_FILE}"
  exit 1
fi

source "${VENV_ACTIVATE}"
echo "Using virtual environment: ${VENV_DIR}" | tee -a "${LOG_FILE}"
echo "Python executable: $(command -v python)" | tee -a "${LOG_FILE}"

python3 pipeline/data_polling.py obs \
  --start-dt "${START_DT}" \
  --end-dt "${END_DT}" \
  2>&1 | tee -a "${LOG_FILE}"

EXIT_CODE=${PIPESTATUS[0]}

echo "" | tee -a "${LOG_FILE}"
if [[ ${EXIT_CODE} -eq 0 ]]; then
  echo "Observation polling completed successfully" | tee -a "${LOG_FILE}"
else
  echo "Observation polling failed with exit code ${EXIT_CODE}" | tee -a "${LOG_FILE}"
fi

exit ${EXIT_CODE}
