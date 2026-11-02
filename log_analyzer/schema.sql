-- Log Analyzer run tracking, in atlas_db (applied idempotently by log_analyzer/db.py at startup).

CREATE SCHEMA IF NOT EXISTS log_analyzer;

-- One row per triggered analysis.
CREATE TABLE IF NOT EXISTS log_analyzer.runs (
    id              BIGSERIAL PRIMARY KEY,
    mode            TEXT NOT NULL CHECK (mode IN ('ota', 'devices')),
    environment     TEXT NOT NULL DEFAULT 'stag' CHECK (environment IN ('stag', 'prod')),
    ota_version     TEXT,                       -- mode=ota: the requested OTA (also the report filter)
    requested_devices TEXT[] NOT NULL DEFAULT '{}',  -- mode=devices: the given list
    devices         TEXT[] NOT NULL DEFAULT '{}',    -- devices actually analysed (resolved for mode=ota)
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    product         TEXT,
    options         JSONB NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    stage           TEXT,                       -- current step while running (resolve, download, analyse, ...)
    progress        JSONB NOT NULL DEFAULT '{}',
    error           TEXT,
    device_errors   JSONB NOT NULL DEFAULT '{}',
    pid             INTEGER,
    requested_by    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    duration_s      DOUBLE PRECISION,
    output_dir      TEXT,
    log_path        TEXT
);
CREATE INDEX IF NOT EXISTS runs_created_at_idx ON log_analyzer.runs (created_at DESC);
CREATE INDEX IF NOT EXISTS runs_status_idx ON log_analyzer.runs (status);
-- Added after the first release: staging/production picks the bucket, device db and log cache
ALTER TABLE log_analyzer.runs ADD COLUMN IF NOT EXISTS environment TEXT NOT NULL DEFAULT 'stag'
    CHECK (environment IN ('stag', 'prod'));
CREATE INDEX IF NOT EXISTS runs_environment_idx ON log_analyzer.runs (environment, id DESC);

-- Timing of every step of a run (resolve, download per device, read/grep per device-day, evaluate, ...).
CREATE TABLE IF NOT EXISTS log_analyzer.run_steps (
    id          BIGSERIAL PRIMARY KEY,
    run_id      BIGINT NOT NULL REFERENCES log_analyzer.runs (id) ON DELETE CASCADE,
    step        TEXT NOT NULL,
    device      TEXT,
    day         DATE,
    status      TEXT NOT NULL DEFAULT 'ok',
    started_at  TIMESTAMPTZ,
    duration_s  DOUBLE PRECISION NOT NULL,
    detail      JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS run_steps_run_idx ON log_analyzer.run_steps (run_id, step);

-- One row per OTA report a run produced.
CREATE TABLE IF NOT EXISTS log_analyzer.run_reports (
    run_id              BIGINT NOT NULL REFERENCES log_analyzer.runs (id) ON DELETE CASCADE,
    ota_version         TEXT NOT NULL,
    devices             INTEGER NOT NULL,
    boots               INTEGER NOT NULL,
    pass                INTEGER NOT NULL DEFAULT 0,
    fail                INTEGER NOT NULL DEFAULT 0,
    not_triggered       INTEGER NOT NULL DEFAULT 0,
    config_keys         INTEGER NOT NULL DEFAULT 0,
    config_keys_failing INTEGER NOT NULL DEFAULT 0,
    config_error        TEXT,
    report_path         TEXT NOT NULL,
    PRIMARY KEY (run_id, ota_version)
);
-- Report summary (devices active / detailed logs, drive time, low power, feature sets)
ALTER TABLE log_analyzer.run_reports ADD COLUMN IF NOT EXISTS summary JSONB NOT NULL DEFAULT '{}';
