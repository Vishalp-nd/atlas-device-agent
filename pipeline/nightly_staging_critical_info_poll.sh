#!/bin/bash
# Nightly staging critical info report + critical bug prep runner.
# Intended for cron at 00:00; runs yesterday -> today as a one-day window.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"
VENV_ACTIVATE="${VENV_DIR}/bin/activate"
LOG_DIR="${SCRIPT_DIR}/logs"
POLL_TZ="Asia/Kolkata"
TIMESTAMP=$(TZ="${POLL_TZ}" date +"%Y%m%d_%H%M%S")
LOG_FILE="${LOG_DIR}/nightly_staging_critical_info_poll_${TIMESTAMP}.log"
STAGING_CINFO_DEVICE_IDS="103402300080,103452403627,103182502303,103182502327,103182502272,6603015518,6603015625,6603005180,103182502310,6603008503,6603105743"
STAGING_CINFO_OTAS="5.6.16.rc.5,4.6.16.rc.5"
STAGING_CINFO_OUTPUT_DIR="${REPO_ROOT}/OUTPUT/staging_critical_info_reports"

mkdir -p "${LOG_DIR}"

START_DATE=$(TZ="${POLL_TZ}" date -d "yesterday" +"%Y-%m-%d")
END_DATE=$(TZ="${POLL_TZ}" date +"%Y-%m-%d")
START_DT="${START_DATE} 00:00:00"
END_DT="${END_DATE} 00:00:00"

echo "=== Nightly Staging Critical Info Poll ===" | tee -a "${LOG_FILE}"
echo "Run timestamp: ${TIMESTAMP}" | tee -a "${LOG_FILE}"
echo "Timestamp (${POLL_TZ}): $(TZ="${POLL_TZ}" date)" | tee -a "${LOG_FILE}"
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

set +e
echo "=== Staging Critical Info Report ===" | tee -a "${LOG_FILE}"
python3 pipeline/staging_critical_info_report.py \
  --start-date "${START_DATE}" \
  --end-date "${END_DATE}" \
  --deviceid "${STAGING_CINFO_DEVICE_IDS}" \
  --ota "${STAGING_CINFO_OTAS}" \
  2>&1 | tee -a "${LOG_FILE}"
STAGING_REPORT_EXIT_CODE=${PIPESTATUS[0]}

echo "" | tee -a "${LOG_FILE}"
echo "=== Critical Bug Prep ===" | tee -a "${LOG_FILE}"
CRITICAL_BUG_PREP_EXIT_CODE=0
IFS=',' read -r -a STAGING_OTA_ARRAY <<< "${STAGING_CINFO_OTAS}"
for ota_version in "${STAGING_OTA_ARRAY[@]}"; do
  ota_version="${ota_version// /}"
  [[ -z "${ota_version}" ]] && continue
  report_path="${STAGING_CINFO_OUTPUT_DIR}/staging_critical_info_${ota_version}_${START_DATE}_${END_DATE}.html"
  if [[ ! -f "${report_path}" ]]; then
    echo "Expected staging report not found, skipping bug prep: ${report_path}" | tee -a "${LOG_FILE}"
    CRITICAL_BUG_PREP_EXIT_CODE=1
    continue
  fi

  python3 pipeline/critical_bug_prep.py \
    --report "${report_path}" \
    2>&1 | tee -a "${LOG_FILE}"
  prep_exit_code=${PIPESTATUS[0]}
  if [[ ${prep_exit_code} -ne 0 ]]; then
    CRITICAL_BUG_PREP_EXIT_CODE=${prep_exit_code}
  fi
done
set -e

echo "" | tee -a "${LOG_FILE}"
if [[ ${STAGING_REPORT_EXIT_CODE} -eq 0 ]]; then
  echo "Staging critical info report completed successfully" | tee -a "${LOG_FILE}"
else
  echo "Staging critical info report failed with exit code ${STAGING_REPORT_EXIT_CODE}" | tee -a "${LOG_FILE}"
fi

if [[ ${CRITICAL_BUG_PREP_EXIT_CODE} -eq 0 ]]; then
  echo "Critical bug prep completed successfully" | tee -a "${LOG_FILE}"
else
  echo "Critical bug prep failed with exit code ${CRITICAL_BUG_PREP_EXIT_CODE}" | tee -a "${LOG_FILE}"
fi

if [[ ${STAGING_REPORT_EXIT_CODE} -ne 0 ]]; then
  exit ${STAGING_REPORT_EXIT_CODE}
fi

exit ${CRITICAL_BUG_PREP_EXIT_CODE}