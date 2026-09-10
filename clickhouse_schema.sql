-- ============================================================
-- Table 1: observation_data
-- One row per observation/metadata JSON file.
-- Join key to video_metadata: file_name
--
-- Types below are reconciled against EXTRACTEDDATA_SCHEMA_COLUMNS in
-- pipeline/observation_extraction.py (the proven Postgres column types for
-- these same fields), adjusted only where the live values need something
-- ClickHouse-native (JSON blobs -> String, model lists -> Array(String),
-- bools -> UInt8). See it before changing a type here.
-- ============================================================
DROP TABLE IF EXISTS observation_data;

CREATE TABLE observation_data
(
    ota                          Nullable(String),
    product_line                 Nullable(String),
    udid                         Nullable(String),
    file_name                    String,                      -- join key
    file_timestamp               Nullable(Float64),
    start_time                   Nullable(DateTime64(3)),
    end_time                     Nullable(DateTime64(3)),
    ignition_status              Nullable(Int64),
    uptime                       Nullable(Int64),
    service_uptime               Nullable(Int64),
    privacymode                  Nullable(Int64),
    dismode                      Nullable(String),
    voltage                      Nullable(Float64),
    processing_mode              Nullable(Int64),
    inertial_processed           Nullable(UInt8),
    vision_processed             Nullable(UInt8),
    nrt_status                   Nullable(String),
    tripno                       Nullable(String),
    videometadatastatus          Nullable(UInt64),
    min_speed                    Nullable(Float64),
    max_speed                    Nullable(Float64),
    sensormetadata_count         Nullable(UInt64),
    driverinvariantsession       Nullable(String),
    driverid                     Nullable(String),
    vehclass                     Nullable(String),
    vehicleid                    Nullable(String),
    cameras                      Nullable(Int64),
    prevvideoname                Nullable(String),
    current_videoname            Nullable(String),
    nextvideoname                Nullable(String),
    devicemodes_itemscount       Nullable(UInt64),
    inference_data_itemscount    Nullable(UInt64),
    canmetadata                  Nullable(String),             -- raw JSON array
    alerts_data_num_alerts       Nullable(UInt64),
    alerts_data                  Nullable(String),             -- raw JSON
    audio_events_num_alerts      Nullable(UInt64),
    audio_events_data            Nullable(String),             -- raw JSON
    events_data_num_alerts       Nullable(UInt64),
    events_data                  Nullable(String),             -- raw JSON
    metadatastatus                String DEFAULT 'full',
    device_id                    String,
    s3_path                      Nullable(String),
    speed_data                   Nullable(String),             -- raw JSON {"speed":[...]}
    starttime                    Nullable(String),             -- raw epoch, kept as text like Postgres
    starttimeld                  Nullable(String),
    inwardstarttime              Nullable(String),
    inwardstarttimeld            Nullable(String),
    rssi                         Nullable(Int64),
    vin                          Nullable(String),
    can_firmware_ver             Nullable(String),
    offset                       Nullable(Int64),
    session_embedding            Nullable(String),             -- raw JSON
    burst_mode                   Nullable(String),             -- raw JSON
    fuel_report                  Nullable(String),             -- raw JSON
    can_src                      Nullable(String),
    can_sn                       Nullable(String),
    engine_status                Nullable(String),
    protocol_info                Nullable(String),
    idling_report                Nullable(String),             -- raw JSON
    tc_recommendation            Nullable(String),
    num_frames_out               Nullable(String),
    num_frames_in                Nullable(UInt64),
    num_frames_dms               Nullable(UInt64),
    num_alerts                   Nullable(UInt64),
    inward_models_processed      Array(String),
    outward_models_processed     Array(String),
    dms_models_processed          Array(String),
    is_inward_processed          Nullable(UInt8),
    is_dms_processed              Nullable(UInt8),
    irled_status                  Nullable(Int64),
    irled_states_timestamp        Nullable(String),
    irled_states_status           Nullable(String),
    faceImageCaptured             Nullable(UInt8),
    obs_filetype                  Nullable(String),
    audioEnable                   Nullable(Int64),
    user_generated_alert          Nullable(String),            -- raw JSON array
    rtc_valid                     Nullable(String),             -- observed value is text ('1'), not boolean
    rtc_jump_from                 Nullable(Int64),
    rtc_jump_to                   Nullable(Int64),
    session_count                 Nullable(String),
    valid_gps_entries             Nullable(UInt64),
    gps_start_time                Nullable(Int64),
    gps_end_time                  Nullable(Int64),
    nw_source                     Nullable(String),
    sinr                          Nullable(Float64),
    nw_recorded_time              Nullable(Int64),
    idle                          Nullable(Int64),
    obdformat                     Nullable(String),
    is_inward_cam_obstructed      Nullable(UInt8),
    has_multi_lane                 UInt8 DEFAULT 0,
    has_road_boundary_tracks        UInt8 DEFAULT 0,
    has_ipc_events                  UInt8 DEFAULT 0,
    is_hd_file                      UInt8 DEFAULT 0,
    inward_vision_processed         Nullable(UInt8)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ifNull(start_time, toDateTime64(0, 3)))
ORDER BY (device_id, file_name)
SETTINGS index_granularity = 8192, allow_nullable_key = 1;


-- ============================================================
-- Table 2: video_metadata
-- One row per element of the `videoMetaData` array.
-- Join key to observation_data: file_name
-- ============================================================
DROP TABLE IF EXISTS video_metadata;

CREATE TABLE video_metadata
(
    file_name       String,                 -- join key -> observation_data.file_name
    device_id       String,                 -- denormalized, avoids a join for device-scoped queries
    start_time      Nullable(DateTime64(3)),-- denormalized from observation_data, same value for all rows of a file_name
    end_time        Nullable(DateTime64(3)),-- denormalized from observation_data, same value for all rows of a file_name
    seq_no          UInt16,                 -- position of this entry within the videoMetaData array
    valid           Nullable(UInt8),
    altitude        Nullable(Float64),
    bearing         Nullable(Float64),
    accuracy        Nullable(Float64),
    lat             Nullable(Float64),
    long            Nullable(Float64),
    speed           Nullable(Float64),
    raw_timestamp   Nullable(UInt64),
    altitudeMSL     Nullable(Float64),
    timestamp       Nullable(DateTime64(3))
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ifNull(start_time, toDateTime64(0, 3)))
ORDER BY (device_id, start_time, file_name, seq_no)
SETTINGS index_granularity = 8192, allow_nullable_key = 1;


-- ============================================================
-- Example join
-- ============================================================
-- SELECT o.file_name, o.tripno, o.device_id, v.lat, v.long, v.speed, v.timestamp
-- FROM observation_data AS o
-- INNER JOIN video_metadata AS v ON o.file_name = v.file_name
-- WHERE o.device_id = 'xxxx'
--   AND o.start_time BETWEEN '2026-09-01 00:00:00' AND '2026-09-02 00:00:00';
--
-- Since video_metadata now carries its own start_time/end_time (denormalized per file_name),
-- the same range filter can be pushed directly onto video_metadata without a join:
-- SELECT * FROM video_metadata
-- WHERE start_time BETWEEN '2026-09-01 00:00:00' AND '2026-09-02 00:00:00';
