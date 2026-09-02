#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
# Postgres -> ClickHouse migration for Atlas critical tables
# ------------------------------------------------------------
# Usage:
#   export PGHOST=... PGUSER=... PGPASSWORD=... PGDATABASE=...
#   export START_TS='2026-01-01 00:00:00'
#   export END_TS='2026-02-01 00:00:00'
#   bash migrate_postgres_to_clickhouse.sh
#
# Notes:
# - ClickHouse is installed under /data/nd_test_bot_ws
# - Table migration is in batches of 100000 rows
# - The target tables are:
#     critical_info_priority
#     unique_critical_info
#     criticalinfo_snowflakes_data
# ------------------------------------------------------------

if [ "${EUID}" -ne 0 ]; then
  SUDO="sudo"
else
  SUDO=""
fi

PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-poll_user}"
PGPASSWORD="${PGPASSWORD:-admin}"
PGDATABASE="${PGDATABASE:-atlas_db}"

CH_HOST="${CH_HOST:-127.0.0.1}"
CH_PORT="${CH_PORT:-9000}"
CH_USER="${CH_USER:-default}"
CH_PASSWORD="${CH_PASSWORD:-}"
CH_DATABASE="${CH_DATABASE:-atlas}"

CLICKHOUSE_BASE="/var/lib/clickhouse"
CLICKHOUSE_DATA_DIR="${CLICKHOUSE_DATA_DIR:-${CLICKHOUSE_BASE}/clickhouse_data}"
CLICKHOUSE_LOG_DIR="${CLICKHOUSE_LOG_DIR:-${CLICKHOUSE_BASE}/clickhouse_logs}"
CLICKHOUSE_TMP_DIR="${CLICKHOUSE_TMP_DIR:-${CLICKHOUSE_BASE}/clickhouse_tmp}"
CLICKHOUSE_USER_FILES_DIR="${CLICKHOUSE_USER_FILES_DIR:-${CLICKHOUSE_BASE}/clickhouse_user_files}"
CLICKHOUSE_FORMAT_DIR="${CLICKHOUSE_FORMAT_DIR:-${CLICKHOUSE_BASE}/clickhouse_format_schemas}"
CSV_TMP_DIR="${CSV_TMP_DIR:-${CLICKHOUSE_BASE}/clickhouse_csv_tmp}"
BATCH_SIZE="${BATCH_SIZE:-100000}"

START_TS="${START_TS:-}"
END_TS="${END_TS:-}"

export DEBIAN_FRONTEND=noninteractive

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    log "Required command not found: $1"
    return 1
  }
}

clickhouse_user_exists() {
  getent passwd clickhouse >/dev/null 2>&1
}

ensure_clickhouse_server_installed() {
  if ! command -v clickhouse-client >/dev/null 2>&1 || ! clickhouse_user_exists; then
    install_clickhouse
  fi
}

install_clickhouse() {
  log "Installing ClickHouse and PostgreSQL client dependencies..."

  ${SUDO} apt-get update
  ${SUDO} apt-get install -y --no-install-recommends \
    apt-transport-https ca-certificates wget gnupg tzdata postgresql-client

  ${SUDO} apt-get update
  ${SUDO} apt-get install -y --no-install-recommends clickhouse-server clickhouse-client
}

configure_clickhouse_mount() {
  log "Configuring ClickHouse data directories on ${CLICKHOUSE_BASE}"

  ${SUDO} mkdir -p "$CLICKHOUSE_DATA_DIR" "$CLICKHOUSE_LOG_DIR" "$CLICKHOUSE_TMP_DIR" "$CLICKHOUSE_USER_FILES_DIR" "$CLICKHOUSE_FORMAT_DIR" "$CSV_TMP_DIR"

  if clickhouse_user_exists; then
    ${SUDO} chown -R clickhouse:clickhouse "$CLICKHOUSE_DATA_DIR" "$CLICKHOUSE_LOG_DIR" "$CLICKHOUSE_TMP_DIR" "$CLICKHOUSE_USER_FILES_DIR" "$CLICKHOUSE_FORMAT_DIR" "$CSV_TMP_DIR"
  else
    log "ClickHouse system user is missing after installation; skipping directory ownership update."
  fi

  ${SUDO} mkdir -p /etc/clickhouse-server/config.d
  ${SUDO} tee /etc/clickhouse-server/config.d/atlas_mount.xml >/dev/null <<EOF
<clickhouse>
    <path>${CLICKHOUSE_DATA_DIR}</path>
    <tmp_path>${CLICKHOUSE_TMP_DIR}</tmp_path>
    <user_files_path>${CLICKHOUSE_USER_FILES_DIR}</user_files_path>
    <format_schema_path>${CLICKHOUSE_FORMAT_DIR}</format_schema_path>
    <logger>
        <errorlog>${CLICKHOUSE_LOG_DIR}/clickhouse-server.err.log</errorlog>
        <size>1000M</size>
    </logger>
</clickhouse>
EOF

  ${SUDO} chown root:root /etc/clickhouse-server/config.d/atlas_mount.xml
  ${SUDO} chmod 644 /etc/clickhouse-server/config.d/atlas_mount.xml
}

start_clickhouse() {
  log "Starting ClickHouse service..."
  if command -v systemctl >/dev/null 2>&1; then
    ${SUDO} systemctl enable clickhouse-server >/dev/null 2>&1 || true
    ${SUDO} systemctl restart clickhouse-server || ${SUDO} service clickhouse-server start || true
  else
    ${SUDO} service clickhouse-server start || true
  fi

  for i in $(seq 1 30); do
    if clickhouse-client --host "$CH_HOST" --port "$CH_PORT" --user "$CH_USER" --password "$CH_PASSWORD" --query "SELECT 1" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  log "ClickHouse did not become ready in time."
  exit 1
}

create_clickhouse_database() {
  log "Ensuring ClickHouse database '${CH_DATABASE}' exists..."
  clickhouse-client --host "$CH_HOST" --port "$CH_PORT" --user "$CH_USER" --password "$CH_PASSWORD" \
    --query "CREATE DATABASE IF NOT EXISTS \"${CH_DATABASE}\";"
}

ensure_clickhouse_column_type() {
  local table_name="$1"
  local column_name="$2"
  local expected_type="$3"
  local current_type

  current_type=$(clickhouse-client --host "$CH_HOST" --port "$CH_PORT" --user "$CH_USER" --password "$CH_PASSWORD" --database "$CH_DATABASE" \
    --query "SELECT type FROM system.columns WHERE database = '${CH_DATABASE}' AND table = '${table_name}' AND name = '${column_name}' LIMIT 1" 2>/dev/null || true)

  if [ -n "$current_type" ] && [ "$current_type" != "$expected_type" ]; then
    log "Dropping ${table_name}: ${column_name} is ${current_type}, expected ${expected_type}"
    clickhouse-client --host "$CH_HOST" --port "$CH_PORT" --user "$CH_USER" --password "$CH_PASSWORD" --database "$CH_DATABASE" \
      --query "DROP TABLE IF EXISTS ${table_name};"
  fi
}

reconcile_clickhouse_schema() {
  ensure_clickhouse_column_type critical_info_priority CODE_AUX Int64
  ensure_clickhouse_column_type unique_critical_info CODE_AUX Int64
  ensure_clickhouse_column_type criticalinfo_snowflakes_data CODE_AUX Int64
  ensure_clickhouse_column_type criticalinfo_snowflakes_data TIMESTAMP DateTime
  ensure_clickhouse_column_type criticalinfo_snowflakes_data UPSERT_TIME DateTime
  ensure_clickhouse_column_type criticalinfo_snowflakes_data LOADED_TO_SNOWFLAKE_ON DateTime
}

create_table_critical_info_priority() {
  log "Creating ClickHouse table: critical_info_priority"
  clickhouse-client --host "$CH_HOST" --port "$CH_PORT" --user "$CH_USER" --password "$CH_PASSWORD" --database "$CH_DATABASE" --query "
    CREATE TABLE IF NOT EXISTS critical_info_priority (
      \"CODE\" Float64,
      \"CODE_AUX\" Int64,
      \"DESCRIPTION\" String,
      \"VERSION\" String,
      \"TRIGGER_REASON\" String,
      \"PRIORITY\" String
    ) ENGINE = MergeTree ORDER BY (\"CODE\", \"CODE_AUX\");
  "
}

create_table_unique_critical_info() {
  log "Creating ClickHouse table: unique_critical_info"
  clickhouse-client --host "$CH_HOST" --port "$CH_PORT" --user "$CH_USER" --password "$CH_PASSWORD" --database "$CH_DATABASE" --query "
    CREATE TABLE IF NOT EXISTS unique_critical_info (
      \"CODE\" Float64,
      \"CODE_AUX\" Int64,
      \"TYPE\" String,
      description_pattern String,
      sample_description String
    ) ENGINE = MergeTree ORDER BY (\"CODE\", \"CODE_AUX\", \"TYPE\");
  "
}

create_table_criticalinfo_snowflakes_data() {
  log "Creating ClickHouse table: criticalinfo_snowflakes_data"
  clickhouse-client --host "$CH_HOST" --port "$CH_PORT" --user "$CH_USER" --password "$CH_PASSWORD" --database "$CH_DATABASE" --query "
    CREATE TABLE IF NOT EXISTS criticalinfo_snowflakes_data (
      \"DEVICE_ID\" String,
      \"TIMESTAMP\" DateTime,
      \"PROCESS_NAME\" String,
      \"CODE\" Float64,
      \"CODE_AUX\" Int64,
      \"COUNT\" UInt64,
      \"DESCRIPTION\" String,
      \"DEVICE_VERSION\" String,
      \"SYS_UPTIME\" Float64,
      \"TENANT_ID\" UInt64,
      \"S3_PATH\" String,
      \"UPSERT_TIME\" DateTime,
      \"LOADED_TO_SNOWFLAKE_ON\" DateTime,
      type String
    ) ENGINE = ReplacingMergeTree
      PARTITION BY toYYYYMM(\"TIMESTAMP\")
      ORDER BY (\"DEVICE_ID\", \"TIMESTAMP\", \"PROCESS_NAME\", \"CODE\", \"DESCRIPTION\");
  "
}

migrate_generic_table() {
  local table_name="$1"
  local before_count
  local after_count
  local select_sql="SELECT * FROM public.${table_name}"

  before_count=$(psql "postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}" -Atqc "SELECT COUNT(*) FROM public.${table_name};")
  log "Migrating ${table_name}: ${before_count} rows"

  if [ "$before_count" -eq 0 ]; then
    log "Skipping ${table_name}: no rows to migrate"
    return 0
  fi

  if [ "$table_name" = "unique_critical_info" ]; then
    select_sql='SELECT "CODE", "CODE_AUX", "TYPE", description_pattern, sample_description FROM public.unique_critical_info'
  fi

  psql "postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}" -X -c "
    COPY (
      ${select_sql}
    ) TO STDOUT WITH (FORMAT csv, HEADER true)
  " 2>/dev/null | clickhouse-client --host "$CH_HOST" --port "$CH_PORT" --user "$CH_USER" --password "$CH_PASSWORD" --database "$CH_DATABASE" --query "INSERT INTO ${table_name} FORMAT CSVWithNames"

  after_count=$(clickhouse-client --host "$CH_HOST" --port "$CH_PORT" --user "$CH_USER" --password "$CH_PASSWORD" --database "$CH_DATABASE" --query "SELECT count() FROM ${table_name};")
  log "Finished ${table_name}. ClickHouse count: ${after_count}"
}

migrate_criticalinfo_snowflakes_data() {
  if [ -z "$START_TS" ] || [ -z "$END_TS" ]; then
    log "START_TS and END_TS must be set for criticalinfo_snowflakes_data. Example: START_TS='2026-01-01 00:00:00' END_TS='2026-02-01 00:00:00'"
    exit 1
  fi

  local last_timestamp="$START_TS"
  local batch_number=0

  while true; do
    local query
    local batch_count
    local batch_max_timestamp

    query=$(cat <<SQL
      COPY (
        SELECT
          "DEVICE_ID",
          "TIMESTAMP"::timestamp(0) AS "TIMESTAMP",
          "PROCESS_NAME",
          "CODE",
          "CODE_AUX",
          "COUNT",
          "DESCRIPTION",
          "DEVICE_VERSION",
          "SYS_UPTIME",
          "TENANT_ID",
          "S3_PATH",
          "UPSERT_TIME"::timestamp(0) AS "UPSERT_TIME",
          "LOADED_TO_SNOWFLAKE_ON"::timestamp(0) AS "LOADED_TO_SNOWFLAKE_ON",
          type
        FROM public.criticalinfo_snowflakes_data
        WHERE "TIMESTAMP" > '${last_timestamp}'::timestamp
          AND "TIMESTAMP" < '${END_TS}'::timestamp
        ORDER BY "TIMESTAMP"
        LIMIT ${BATCH_SIZE}
      ) TO STDOUT WITH (FORMAT csv, HEADER true)
SQL
)

    batch_count=$(psql "postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}" -Atqc "SELECT COUNT(*) FROM (SELECT 1 FROM public.criticalinfo_snowflakes_data WHERE \"TIMESTAMP\" > '${last_timestamp}'::timestamp AND \"TIMESTAMP\" < '${END_TS}'::timestamp ORDER BY \"TIMESTAMP\" LIMIT ${BATCH_SIZE}) t;")

    if [ "$batch_count" -eq 0 ]; then
      break
    fi

    batch_number=$((batch_number + 1))
    log "Migrating criticalinfo_snowflakes_data batch=${batch_number}, start_ts=${last_timestamp}, rows=${batch_count}"

    psql "postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}" -X -A -F$'\t' -c "$query" | \
      clickhouse-client --host "$CH_HOST" --port "$CH_PORT" --user "$CH_USER" --password "$CH_PASSWORD" --database "$CH_DATABASE" \
      --query "INSERT INTO criticalinfo_snowflakes_data FORMAT CSVWithNames"

    batch_max_timestamp=$(psql "postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}" -Atqc "SELECT COALESCE(to_char(MAX(\"TIMESTAMP\"), 'YYYY-MM-DD HH24:MI:SS'), '') FROM (SELECT \"TIMESTAMP\" FROM public.criticalinfo_snowflakes_data WHERE \"TIMESTAMP\" > '${last_timestamp}'::timestamp AND \"TIMESTAMP\" < '${END_TS}'::timestamp ORDER BY \"TIMESTAMP\" LIMIT ${BATCH_SIZE}) t;")

    if [ -z "$batch_max_timestamp" ] || [ "$batch_max_timestamp" = "$last_timestamp" ]; then
      break
    fi

    last_timestamp="$batch_max_timestamp"
  done

  local total_row_count
  total_row_count=$(psql "postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}" -Atqc "SELECT COUNT(*) FROM public.criticalinfo_snowflakes_data WHERE \"TIMESTAMP\" >= '${START_TS}'::timestamp AND \"TIMESTAMP\" < '${END_TS}'::timestamp;")
  local clickhouse_total
  clickhouse_total=$(clickhouse-client --host "$CH_HOST" --port "$CH_PORT" --user "$CH_USER" --password "$CH_PASSWORD" --database "$CH_DATABASE" --query "SELECT COUNT() FROM criticalinfo_snowflakes_data WHERE \"TIMESTAMP\" >= parseDateTimeBestEffort('${START_TS}') AND \"TIMESTAMP\" < parseDateTimeBestEffort('${END_TS}');")

  log "Finished criticalinfo_snowflakes_data. PostgreSQL rows=${total_row_count}, ClickHouse rows=${clickhouse_total}"
}

main() {
  require_cmd psql
  require_cmd wget

  ensure_clickhouse_server_installed
  require_cmd clickhouse-client

  configure_clickhouse_mount
  start_clickhouse
  create_clickhouse_database
  create_table_critical_info_priority
  create_table_unique_critical_info
  create_table_criticalinfo_snowflakes_data
  reconcile_clickhouse_schema
  create_table_critical_info_priority
  create_table_unique_critical_info
  create_table_criticalinfo_snowflakes_data

  migrate_generic_table critical_info_priority
  migrate_generic_table unique_critical_info
  migrate_criticalinfo_snowflakes_data

  log "Migration complete. Data stored under ${CLICKHOUSE_BASE}"
}

main "$@"
