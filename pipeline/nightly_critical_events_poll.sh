#!/bin/bash
# Nightly critical-events pipeline polling
# Pulls a rolling 24-hour window (this time yesterday -> now) into PostgreSQL
# Runs daily at 01:00 IST via cron

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="${SCRIPT_DIR}/../.venv"
DB_CREDENTIALS="${SCRIPT_DIR}/../db_credentials.ini"
ENV_FILE="${SCRIPT_DIR}/../.env"
LOG_DIR="${SCRIPT_DIR}/logs"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="${LOG_DIR}/nightly_poll_${TIMESTAMP}.log"

# Ensure log directory exists
mkdir -p "${LOG_DIR}"

# Load required env vars from the repo-root .env
if [ ! -f "${ENV_FILE}" ]; then
  echo "Missing .env file at ${ENV_FILE}" | tee -a "${LOG_FILE}"
  exit 1
fi
set -a
source "${ENV_FILE}"
set +a

# ${VAR+x} expands only if VAR is set (even to an empty string), so this
# distinguishes "defined but empty" (allowed) from "missing from .env" (error) -
# mirrors the os.environ check in critical_events_pipeline.py.
if [ -z "${IGNORED_CODES+x}" ]; then
  echo "Missing required env var IGNORED_CODES. Configure it in the repo-root .env (use an empty value to disable ignored-code filtering)." | tee -a "${LOG_FILE}"
  exit 1
fi
if [ -z "${ALLOWED_OTA_VERSIONS+x}" ]; then
  echo "Missing required env var ALLOWED_OTA_VERSIONS. Configure it in the repo-root .env with comma-separated OTA versions." | tee -a "${LOG_FILE}"
  exit 1
fi

# Activate virtual environment
source "${VENV_PATH}/bin/activate"

# Fixed 01:00 -> 01:00 window: yesterday 01:00 to today 01:00 (not the actual run time)
YESTERDAY=$(date -d "yesterday" +"%Y-%m-%d")
TODAY=$(date +"%Y-%m-%d")
START_TS="${YESTERDAY}T01:00:00"
END_TS="${TODAY}T01:00:00"

echo "=== Nightly Critical Events Poll ===" | tee -a "${LOG_FILE}"
echo "Timestamp: $(date)" | tee -a "${LOG_FILE}"
echo "Polling date range: ${START_TS} to ${END_TS}" | tee -a "${LOG_FILE}"
echo "Log file: ${LOG_FILE}" | tee -a "${LOG_FILE}"
echo "" | tee -a "${LOG_FILE}"

# Optional: pass a profile only when explicitly configured.
AWS_PROFILE_ARG=()
if [ -n "${AWS_PROFILE:-}" ]; then
  AWS_PROFILE_ARG=(--aws-profile "${AWS_PROFILE}")
  echo "Using AWS profile from env: ${AWS_PROFILE}" | tee -a "${LOG_FILE}"
else
  echo "Using default AWS credential chain (env/instance role)" | tee -a "${LOG_FILE}"
fi

# Run pipeline with poll_user (no idle timeouts)
cd "${SCRIPT_DIR}"
python3 critical_events_pipeline.py \
  --snowflake-section SNOWFLAKE_DB \
  "${AWS_PROFILE_ARG[@]}" \
  --start-ts "${START_TS}" \
  --end-ts "${END_TS}" \
  --postgres-section POLL_USER_DB \
  --table-name criticalinfo_snowflakes_data \
  --commit-every 5 \
  2>&1 | tee -a "${LOG_FILE}"

EXIT_CODE=$?
echo "" | tee -a "${LOG_FILE}"
if [ $EXIT_CODE -eq 0 ]; then
  echo "✓ Poll completed successfully" | tee -a "${LOG_FILE}"
else
  echo "✗ Poll failed with exit code $EXIT_CODE" | tee -a "${LOG_FILE}"
fi

# Only run the unique-info/priority pipeline if the raw poll succeeded.
if [ $EXIT_CODE -eq 0 ]; then
  echo "" | tee -a "${LOG_FILE}"
  echo "=== Unique-info priority pipeline ===" | tee -a "${LOG_FILE}"
  python3 nightly_priority_pipeline.py \
    --db-section POLL_USER_DB \
    --start-ts "${START_TS}" \
    --end-ts "${END_TS}" \
    2>&1 | tee -a "${LOG_FILE}"
  PRIORITY_EXIT_CODE=${PIPESTATUS[0]}
  if [ $PRIORITY_EXIT_CODE -eq 0 ]; then
    echo "✓ Priority pipeline completed successfully" | tee -a "${LOG_FILE}"
  else
    echo "✗ Priority pipeline failed with exit code $PRIORITY_EXIT_CODE" | tee -a "${LOG_FILE}"
    EXIT_CODE=$PRIORITY_EXIT_CODE
  fi
fi

echo "Completed at: $(date)" | tee -a "${LOG_FILE}"

exit $EXIT_CODE
