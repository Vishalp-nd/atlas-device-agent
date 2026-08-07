---
name: healthstatsmanager-service-validation
description: "Use when: validating HealthStatsManager (HealthStatsManager.service) service behavior from device logs. Covers initialization, config parsing, health stats collection, payload creation, upload, MQ message handling, DB operations, periodic metric sampling, payload field validation, and config-driven features."
argument-hint: "device ID (e.g., /healthstatsmanager-service-validation 103452403525)"
---

# HealthStatsManager (`HealthStatsManager.service`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the service —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads `.py` test case files from `tests/HEALTHSTATSMANAGER/`
> for actual log patterns, device-type paths, and acceptance criteria — this skill does NOT duplicate those.

---

## Service Overview

`HealthStatsManager` is a critical systemd service that collects device health metrics, constructs HS payload JSON, and uploads it to the cloud via HTTPS.
It handles periodic hardware sampling (CPU, GPU, memory, storage, thermal), ingests analytics data from other services via an IPC message queue, and persists all collected data in `healthstats.db`.
The service interacts with `uploader`, `circular_buffer`, `DiagnosticsManager`, `APM`, and `CONN_MGR_MAIN` via IPC sockets (`/dev/shm/MSGQ/`) and shared SQLite databases.

**Process name:** `HealthStatsManager`
**Log file:** `health.log` (path defined per device type in device config)
**Primary config sections:** `[healthstats]`, `[device_overspeed_v2]`

---

## Service Flows

### Flow 1: Service Initialization & Config Parsing

**What happens:** On startup the service checks for any existing payload leftover from a prior session (`check_for_existing_payload`), then parses `bagheera_override.ini` for `db_version` and interval overrides and `nddevice.ini` / `bagheera_config.ini` for `deviceType`. It verifies or creates required log and backup directories, then launches the `healthstats` and `videohealthstats` threads and initialises the IPC message queue.

**When active:** Always at boot / service restart
**Frequency:** Once per service start
**Cross-service impact:** Directories consumed by `uploader`; `deviceType` drives payload routing to the correct cloud endpoint.

**Test cases that validate this flow:**
| Test Case ID | Test File Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_hs_1271` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1271_override_config_parse.py` | `db_version is set to : 2` appears after override config is set | — |
| `TC_hs_1273` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1273_device_config_parse.py` | `deviceType from the deviceConfig.ini is <type>` appears in log | — |
| `TC_hs_1287` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1287_log_and_backup_folder_creation.py` | `/log/health`, `/log/health/backup`, `/log/health_c` directories exist | — |
| `TC_hs_1293` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1293_service_status_check.py` | `HealthStatsManager.service` is in active (running) state | — |

**Key log patterns:**
```
Entering check_for_existing_payload
Exiting check_for_existing_payload
Entering:::validateDataAndCreateBackup:::
db_version is set to : 2
db_version not present in BAGHEERA_OVERRIDE_CONFIG
healthstats_secs not present in BAGHEERA_OVERRIDE_CONFIG
videohealthstats_secs not present in BAGHEERA_OVERRIDE_CONFIG
cpu_gpu_info_secs not present in BAGHEERA_OVERRIDE_CONFIG
process_info_secs not present in BAGHEERA_OVERRIDE_CONFIG
deviceType from the deviceConfig.ini is bagheera3
===========starting healthstats thread=================
===========starting videohealthstats thread=================
Starting MQ Healthstats
Starting MQ Healthstats Long
MQ created with key: <n>
mq object: Key=<n>, id=<n>
calling subscribe: ipc:///dev/shm/MSGQ/<n>
```

---

### Flow 2: Health Stats Collection (loghealthstatsBagheera)

**What happens:** The `healthstats` thread calls `loghealthstatsBagheera()` on a ~60-second cycle. Each cycle: collects external/internal storage info, version info, thermal/fan data, and service status via `systemctl`. Results are stored to `healthstats.db` under `health_info:*` primary keys. Errors from `get_service_status` (service not found in grep) are non-fatal.

**When active:** Always after initialization
**Frequency:** Every ~60 seconds (configurable via `[healthstats] healthstats_secs`)
**Cross-service impact:** Reads systemd status of dependent services (`uploader`, `circular_buffer`); thermal commands toggle `/sys/devices/pwm-fan/tach_enable`.

**Test cases that validate this flow:**
| Test Case ID | Test File Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_hs_83`   | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_83_sample_healthdata.py`   | `loghealthstatsBagheera` cycle log entries present | — |
| `TC_hs_1263` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1263_service_uptime.py` | Service uptime of `uploader` and `circular_buffer` valid | — |
| `TC_hs_113`  | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_113_sample_process_info.py`  | `process_name.*HealthStatsManager` present in process info sample | — |

**Key log patterns:**
```
==========Entering  loghealthstatsBagheera() function=====================
counter updated to <n> data
status updated to 0 data
==========Entering the  external_storage_info function=====================
SDCard / external eMMC mounted_info mounted
==========Entering the  internal_storage_info function=====================
========= Entering  get_version_info() function =========
==========Entering the  thermal_temp_info() function=====================
echo 1 > /sys/devices/pwm-fan/tach_enable command result is = 0
Setting the fan tac_enable to 0
==========Entering the get_service_status() function=====================
Active not found in line grep :        # ERROR — non-fatal, service not found by grep
==========Exiting  loghealthstatsBagheera() function=====================
```

---

### Flow 3: Video HealthStats / Payload Creation (videohealthstats)

**What happens:** The `videohealthstats` thread (`Entering:::videohealthstats:::`) triggers the full HS payload lifecycle. It reads MDVR serial, camera config, and `db_version`, then builds a JSON payload (`Successfully jsonFile has been created`), validates it (`isValidJSON`), calls `UpdateHealthStatusBagheera` to embed critical event info, clears empty fields, creates a gzip archive, moves it to the backup folder, and logs file sync confirmations.

**When active:** Always; upload triggers the next cycle after success
**Frequency:** Every ~180 seconds (configurable via `[healthstats] videohealthstats_secs`)
**Cross-service impact:** `critical_event_infoString[phm]` entries sourced from DiagnosticsManager/PHM; backup file consumed by the upload sub-flow.

**Test cases that validate this flow:**
| Test Case ID | Test File Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_hs_68`   | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_68_base_payload_creation.py`   | JSON creation + validation + gzip sync log pattern | `BG4-783` |
| `TC_hs_70`   | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_70_validate_hs_payload_creation.py`   | Full payload creation cycle present in log | `BG4-783` |
| `TC_hs_71`   | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_71_processing_health_info_data.py`   | `health_info` data processing logs present | `BG4-783` |
| `TC_hs_72`   | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_72_processing_critical_info_data.py`   | `critical_event_infoString[phm]` entries present | `BG4-626` |
| `TC_hs_90`   | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_90_create_old_payload.py`   | Old payload creation handled (backup rotation) | — |
| `TC_hs_91`   | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_91_data_added_to_hs_db.py`   | Data added to healthstats.db after payload creation | — |
| `TC_hs_1274` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1274_multiple_payload_backup.py` | Multiple backup files accumulate correctly | — |
| `TC_hs_1284` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1284_payload_objects_num_check.py` | Payload object count matches expected number | — |
| `TC_hs_1313` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1313_validate_payload_zero_size.py` | Zero-size payload not uploaded | — |

**Key log patterns:**
```
Entering:::videohealthstats:::
==========Entering  get_mdvr_serial_number()  function=====================
db_version is : 2
Successfully jsonFile has been created.
Entering:::UpdateHealthStatusBagheera:::
critical_event_infoString[phm]: {<dict>}
Exiting:::UpdateHealthStatusBagheera:::
Records found: <n>
Records found UDID: <n>
File sync finished: /home/ubuntu/.nddevice//log/health/videoData.json, time taken: <n>
DB HS created True|False
reset masterdata message sent to diagnostic
Entering:::deleteEmptyFieldsInJson:::
cleared empty fields!!
Entering:::isValidJSON:::
Exiting:::isValidJSON:::
Entering:::createZipOfHealthLogs:::
Total no of health files is equal to 1
File sync finished: /home/ubuntu/.nddevice//log/health/videoData.json.gz, time taken: <n>
Entering:::createZipBackup:::
Moving zip to backup
File sync finished: /home/ubuntu/.nddevice//log/health/backup/videoData.json.gz_<timestamp>.gz, time taken: <n>
Exiting:::createZipBackup:::
Exiting:::validateDataAndCreateBackup:::
```

---

### Flow 4: Payload Upload

**What happens:** After a successful gzip, the service obtains a JWT token, constructs a `curl -X POST` command with device headers, uploads the `.gz` file to `idms.netradyne.com`, logs `Upload of Health Stats successful`, then deletes the file from disk. It then sweeps the backup folder for any pending old `.gz` files and uploads each in turn.

**When active:** Always; requires network connectivity
**Frequency:** After each videohealthstats cycle (~180s)
**Cross-service impact:** Upload goes to `idms.netradyne.com`; failure leaves files in backup folder consumed by the next cycle.

**Test cases that validate this flow:**
| Test Case ID | Test File Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_hs_79`   | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_79_upload_hs_payload.py`   | `curl -X POST` and `File uploaded, deleting from disk True` present | `AN-28160` |
| `TC_hs_81`   | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_81_upload_hs_payload_backup.py`   | Backup file upload path `backup/videoData.json.gz_` present | — |
| `TC_hs_53`   | *(no .py file — test case not yet implemented)*   | Dummy backup payload check (backup file exists before upload) | — |
| `TC_hs_1280` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1280_back_to_back_upload.py` | Back-to-back upload cycle completes without error | `BG4-856`, `AN-28160` |

**Key log patterns:**
```
Entering:::uploadHealthLogs:::
JWT response code = 1
JWT auth header = X-Device-JWT: <token>
curl -X POST -H "X-DeviceType: bagheera3" -H "X-Device-JWT: ..." -H "X-DeviceId: <id>" ... @/home/ubuntu/.nddevice//log/health/videoData.json.gz
Upload of Health Stats successful
File uploaded, deleting from disk True
Found <n> old zip files...
curl -X POST ... @/home/ubuntu/.nddevice//log/health/backup/videoData.json.gz_<ts>.gz
Exiting:::videohealthstats:::
```

---

### Flow 5: MQ Message Handling & DB Storage

**What happens:** The `MQ Healthstats Long` thread subscribes to IPC sockets (`/dev/shm/MSGQ/`) and receives JSON messages from other services (APM, CONN_MGR, GPS, DiagnosticsManager, CB). Each received message is parsed to extract a `primaryKey`, looked up in `healthstats.db`, and either inserted (`sessionCreated=1`) or updated (`sessionUpdated=1`). The DB stores data under keys like `health_info:cpu_info`, `health_analytics_<session>`, `config_info:analytics_config`, `health_info:gps_info`, etc.

**When active:** Always after initialization; MQ must be available
**Frequency:** Event-driven; messages arrive continuously from subscribed services
**Cross-service impact:** APM sends power/ignition data; GPS service sends location; `circular_buffer` sends storage stats; `CONN_MGR_MAIN` sends LTE info.

**Test cases that validate this flow:**
| Test Case ID | Test File Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_hs_91`   | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_91_data_added_to_hs_db.py`   | `sessionCreated=1` / `sessionUpdated=1` appear after service start | — |
| `TC_hs_1260` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1260_db_permissions.py` | `healthstats.db` owned by `root root`, correct permissions | `MOW-731` |
| `TC_hs_1366` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1366_validate_healthstats_db.py` | `healthstats.db` schema: AH table columns ID, SESSION, BODY, STATUS | `MOW-731` |
| `TC_hs_1376` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1376_validate_udid_db.py` | `udid.db` schema: UDID_TABLE columns ID, UDID | — |

**Key log patterns:**
```
Server_name_path /dev/shm/MSGQ/<n>
/dev/shm/MSGQ/<n> IS AVAILABLE
Server setup successful
calling publish: ipc:///dev/shm/MSGQ/<n>, ha
publish_status: True
Received message: {"session": "health_analytics_<hash>", "ts": <epoch>, ...}
offset: <n>
Records found: <n>
primaryKey: health_info:cpu_info
primaryKey: health_info:gpu_info
primaryKey: health_info:gps_info
primaryKey: health_info:apm_info
primaryKey: health_info:apm_thres_info
primaryKey: health_info:Int_eMMC_health_info
primaryKey: health_info:Ext_eMMC_health_info
primaryKey: health_info:lte_network_time_sync
primaryKey: health_analytics_<session>
primaryKey: config_info:analytics_config
primaryKey: config_info:bagheera_config
primaryKey: sam_cnt_info
primaryKey: 0_trip<id>_part<id>_<lat>_<lon>_<spd>_<ts>_y   # trip/alert/observation entries
sessionCreated=1
sessionUpdated=1
```

---

### Flow 6: Periodic Metric Sampling (DB Update Period)

**What happens:** `cpu_info`, `gpu_info`, `free_info`, and `process_info` are sampled via `sendCpuGpuFreeinfoToHealthstats` and `addMasterCpuGpuInfo` helpers at independently configurable intervals. The DB entry for each key is updated at ~60-second or ~120-second intervals depending on the config. The agent verifies update intervals by reading sequential timestamps from `healthstats.db`.

**When active:** Always after initialization
**Frequency:** Every ~60s or ~120s per metric type (configurable via `cpu_gpu_info_secs`, `process_info_secs`)
**Cross-service impact:** None direct; data consumed by payload creation flow.

**Test cases that validate this flow:**
| Test Case ID | Test File Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_hs_1245` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1245_cpu_info_update_period.py` | `health_info:cpu_info` DB entry updates every 59-61s or 118-122s | — |
| `TC_hs_1247` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1247_gpu_info_update_period.py` | `health_info:gpu_info` DB entry update period | `BG4-851` |
| `TC_hs_1248` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1248_free_info_update_period.py` | `health_info:free_info` (memory) DB entry update period | — |
| `TC_hs_1249` | *(no .py file — test case not yet implemented)* | `health_info:process_info` DB entry update period | — |

**Key log patterns:**
```
entering sendCpuGpuFreeinfoToHealthstats
Entering:::addMasterCpuGpuInfo
primaryKey: health_info:cpu_info
primaryKey: health_info:gpu_info
```

---

### Flow 7: Payload Field Content Validation

**What happens:** After payload creation the agent validates the JSON structure on-device. Each `health_info` sub-field has type, range, and completeness constraints. The helper script `healthstats_validate_payload.py` automates field-level checks. This flow does NOT produce distinct log patterns — it validates the content of the payload JSON file itself.

**When active:** After `videohealthstats` cycle creates a `.gz`; tested by blocking upload with iptables so the backup file is retained for inspection.
**Frequency:** Per payload cycle
**Cross-service impact:** None; read-only validation of the produced JSON.

**Test cases that validate this flow:**
| Test Case ID | Test File Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_hs_1324` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1324_validate_process_info.py` | `process_info`: timestamp 13-digit, cpu/mem usage >0, no negatives, ≤3 decimals | `BG4-844` |
| `TC_hs_1325` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1325_validate_cpu_info.py` | `cpu_info`: cpu_no, temp_mean, usage_mean 0-100, freq_mean, *_sd fields | `BG4-843` |
| `TC_hs_1329` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1329_validate_gpu_info.py` | `gpu_info`: temp_mean, freq_mean, *_sd fields | `BG4-851` |
| `TC_hs_1331` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1331_validate_free_info.py` | `free_info`: free/available/shared/buff_cache means all >0 | `BG4-857` |
| `TC_hs_1335` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1335_validate_module_info.py` | `module_info`: required thermal modules present (GPU-therm, CPU-therm, Tboard_tegra) | `BG4-850`, `BG4-843` |
| `TC_hs_1341` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1341_validate_master_cpu_info.py` | `master cpu_info`: aggregate CPU metrics valid | — |
| `TC_hs_1342` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1342_validate_master_gpu_info.py` | `master gpu_info`: aggregate GPU metrics valid | `BG4-851` |
| `TC_hs_1343` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1343_validate_master_free_info.py` | `master free_info`: aggregate memory metrics valid | `BG4-857` |
| `TC_hs_1348` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1348_validate_wifi_network_info.py` | `network_info (wifi)`: SSID, signal, status present | `BG4-512` |
| `TC_hs_1352` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1352_validate_lte_network_info.py` | `network_info (LTE)`: signal, network type present | `DT-3987` |
| `TC_hs_1353` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1353_validate_storage_info.py` | `storage_info`: usedSize, actualSize, spaceleft, partition_id | `BG4-857` |
| `TC_hs_1355` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1355_validate_service_info.py` | `service_info`: service_name, status, timestamp per entry | — |
| `TC_hs_1360` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1360_validate_gps_info.py` | `gps_info`: latitude, longitude, altitude, accuracy present | — |
| `TC_hs_1364` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1364_validate_power_info.py` | `power_info`: voltage, current, temperature present | `BG4-884` |
| `TC_hs_1380` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1380_validate_boot_partition_info.py` | `boot_partition_info`: bootloader version, partition status | — |
| `TC_hs_1419` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1419_validate_inward_analytics_service.py` | `inwardanalytics_service` field present and valid | — |
| `TC_hs_1422` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1422_validate_outward_analytics_service.py` | `outwardanalytics_service` field present and valid | — |
| `TC_hs_1423` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1423_validate_inward_analytics_client.py` | `inwardanalytics_client` field present and valid | — |
| `TC_hs_1428` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1428_validate_outward_analytics_client.py` | `outwardanalytics_client` field present and valid | — |
| `TC_hs_1441` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1441_validate_general_fields.py` | Root-level general fields: mdvr_id, device_id, deviceversion, mac_id, timestamp (13-digit), ver, deviceType, ip_address | `MOW-724`, `DT-3601` |
| `TC_hs_1401` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1401_validate_observation.py` | `observation` sub-fields present (stage, location, size, sensor) | — |
| `TC_hs_1403` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1403_validate_drive_time.py` | Root-level `drive_time` array present and non-empty | — |
| `TC_hs_1450` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1450_validate_alert_info.py` | `alert_info`: recordingstart/recordingend timestamps present | — |
| `TC_hs_1460` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1460_validate_critical_event_info.py` | `critical_event_info`: timestamp, process_name, code, code_aux, sys_uptime, desc, count | `BG4-626` |
| `TC_hs_1462` | `tests/HEALTHSTATSMANAGER/test_tc_healthstatsmanager_1462_validate_video.py` | `video` sub-field: starttime, endtime, duration, size, frame_count present | `BG4-946`, `MOW-720` |

---

### Flow 8: Config-Driven Features (Overspeed v2 & Audio)

**What happens:** When `[device_overspeed_v2] enabled = 1`, the service includes overspeed tracking data in the HS payload. When audio capture is active, an `audio` sub-field is included in trip entries in `healthstats.db` (sdcard copy info). These are optional payload sections gated by config.

**When active:** `device_overspeed_v2` only when config key is set; audio only when audio capture is configured
**Frequency:** Per payload cycle when active
**Cross-service impact:** Overspeed data sourced from `device_overspeed_v2` pipeline; audio from `circular_buffer`.

**Test cases that validate this flow:**
| Test Case ID | Test File Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_hs_1387` | *(no .py file — test case not yet implemented)* | `overspeed_device_v2` field present in payload when config enabled | — |
| `TC_hs_1390` | *(no .py file — test case not yet implemented)* | `audio` sub-field present in trip DB entry (size, sdcard copy info) | — |

---

## Config-Driven Flow Activation

The agent MUST read the device config from `device_data/device_<ID>_config.ini`
before selecting test cases. Use the following mapping:

| Config Section | Config Key | Value | Activates Flow(s) | Test Cases Affected |
|---|---|---|---|---|
| `[healthstats]` | `videohealthstats_secs` | `<N>` (default 180) | Payload Creation cycle interval | `TC_hs_68`, `TC_hs_70`, `TC_hs_79` |
| `[healthstats]` | `healthstats_secs` | `<N>` (default 60) | Health Stats Collection interval | `TC_hs_83`, `TC_hs_1263` |
| `[healthstats]` | `cpu_gpu_info_secs` | `<N>` (default 60) | Periodic Metric Sampling (CPU/GPU) | `TC_hs_1245`, `TC_hs_1247` |
| `[healthstats]` | `process_info_secs` | `<N>` (default 60) | Periodic Metric Sampling (process/free) | `TC_hs_1248`, `TC_hs_1249` |
| `[healthstats]` | `db_version` | `2` | Config Parse validation | `TC_hs_1271` |
| `[device_overspeed_v2]` | `enabled` | `1` | Overspeed v2 payload field | `TC_hs_1387` |
| — | — | — | All other flows (always active) | All remaining TCs |

**Rules:**
- Flows marked "always active" → run their test cases unconditionally
- `device_overspeed_v2` → run `TC_hs_1387` only if `enabled = 1`
- If an interval config key is missing from override config → default values apply (logged as `<key> not present in BAGHEERA_OVERRIDE_CONFIG`)
- Config values in `device_list_config.csv` take precedence if present

---

## Cross-Service Dependencies

| Related Service | Why | When to check its logs |
|---|---|---|
| `uploader` | Uploader service stability affects HS service uptime checks | When validating `TC_hs_1263` (service uptime) |
| `circular_buffer` | CB sends storage event data via MQ; CB uptime checked in service_info | When validating `TC_hs_1353`, `TC_hs_1263` |
| `DiagnosticsManager` | Source of `critical_event_infoString[phm]` entries embedded in payload | When validating `TC_hs_72`, `TC_hs_1460` |
| `APM` | Sends `health_info:apm_info` and `health_info:apm_thres_info` via MQ | When validating `TC_hs_1364` (power_info) |
| `CONN_MGR_MAIN` | LTE modem firmware version appears in critical_event_info | When validating `TC_hs_1352` (LTE network info) |

---

## Flow Dependency Graph

```
boot → [Flow 1: Init] → parse configs → create dirs → launch threads
                      → [Flow 2: healthstats thread] → loghealthstatsBagheera every ~60s → DB writes
                      → [Flow 3: videohealthstats thread] → payload JSON build every ~180s
                                                          → [Flow 4: Upload] → JWT → curl POST → delete
                                                          → backup file retained if upload fails
                      → [Flow 5: MQ Long thread] → subscribe IPC sockets → sessionCreated/Updated
                      → [Flow 6: cpu_gpu thread] → metric sampling at configurable intervals

[Flow 3] depends on [Flow 5] data being in DB (health_info:*, config_info:*)
[Flow 7: Field Validation] → post-creation read of payload JSON → block upload with iptables to retain file
config key [device_overspeed_v2] enabled=1 → [Flow 8: Overspeed v2 field] appears in Flow 3 payload
```

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Determine active flows** using the Config-Driven Flow Activation table above
3. **For each active flow**, read the mapped `.py` test case files from `tests/HEALTHSTATSMANAGER/`
4. **From each test file**, use the acceptance criteria and log patterns defined in the test functions
5. **Search device logs** in `device_logs/<device_id>/health.log` using patterns from the test files and the Key Log Patterns sections above
6. **For payload field tests (Flow 7)**, use on-device commands to gunzip and inspect the backup payload:
   ```bash
   # Find latest backup (bagheera/D450 — sudo needed)
   sudo bash -c 'gzip -dc $(ls -1S /home/ubuntu/.nddevice/log/health/backup/*gz | head -1) > /tmp/payload.json'
   python3 /tmp/healthstats_validate_payload.py /tmp/payload.json <field_name>
   # field_name options: process_info, cpu_info, gpu_info, free_info, module_info,
   #   master_cpu_info, master_gpu_info, master_free_info, wifi_network_info,
   #   lte_network_info, storage_info, service_info, gps_info, power_info,
   #   boot_partition_info, general_fields, alert_info, critical_event_info,
   #   video, drive_time, inwardanalytics_service, outwardanalytics_service,
   #   inwardanalytics_client, outwardanalytics_client
   ```
7. **For DB validation tests (Flow 5)**, run sqlite3 checks:
   ```bash
   sqlite3 /home/ubuntu/.nddevice/db/healthstats.db "PRAGMA table_info(AH);"
   # Expected: 0|ID|INTEGER|0||0  1|SESSION|TEXT|0||0  2|BODY|VARCHAR(255)|0||0  3|STATUS|INT|0||0
   sqlite3 /home/ubuntu/.nddevice/db/udid.db "PRAGMA table_info(UDID_TABLE);"
   # Expected: 0|ID|INTEGER|0||0  1|UDID|TEXT|0||0
   sqlite3 /home/ubuntu/.nddevice/db/healthstats.db "SELECT SESSION FROM AH WHERE SESSION LIKE '%health_info:cpu_info%' ORDER BY ID DESC LIMIT 1;"
   ```
8. **Block upload for offline payload tests** (required by TC_hs_53, TC_hs_1274, TC_hs_1284, TC_hs_1313, all Flow 7 TCs):
   ```bash
   sudo iptables -A OUTPUT -p tcp --dport 443 -j DROP   # before test
   sudo iptables -D OUTPUT -p tcp --dport 443 -j DROP   # restore after
   ```
9. **For cross-service checks**, also search logs of related services listed above
10. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED
