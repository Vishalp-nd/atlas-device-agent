---
name: keepalivemanager-service-validation
description: "Use when: validating Keep Alive Manager (keep_alive_manager) service behavior from device logs. Covers keepalive counter & epoch tracking, log rotation, keepalive API calls, log upload pipeline, observations & EA image upload, syslog archival & health reporting, and SVC cleanup syslog preservation."
argument-hint: "device ID (e.g., /keepalivemanager-service-validation 103452403525)"
---

# Keep Alive Manager (`keep_alive_manager`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the service —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads `.py` test case files from `tests/keep_alive_manager/`
> for actual log patterns, device-type paths, and acceptance criteria — this skill does NOT duplicate those.

---

## Service Overview

`keep_alive_manager` is a crontab-managed service that runs every minute to signal that the device is alive and functioning.
It handles keepalive counter incrementing, epoch time tracking, log rotation checks, critical log zipping and upload, observations/EA image upload triggering, syslog file archival, and health service reporting.
The service interacts with `unifieduploader` (via MSGQ at `/dev/shm/MSGQ/UniUpload`), `health` (syslog data reporting), and `svc` (disk cleanup coordination) via file system and message queue mechanisms.

**Process name:** `keep_alive_manager`
**Log file:** `keep_alive_manager.log` (path defined per device type in each test file)
**Primary config sections:** `[log]`, `[drp]` (in `bagheera_override.ini`), `logconfig.ini`

---

## Service Flows

### Flow 1: Keepalive Counter & Epoch Tracking

**What happens:** Every minute (via crontab), KAM starts, reads the current keepalive counter value from `keepalive_count.txt`, increments it by 1, writes it back, then shuts down. Each invocation also logs `currentEpoch` (current system epoch in seconds) and compares it against `bagheeraLogRotationTime` to determine if log rotation is needed.

**When active:** Always
**Frequency:** Every ~60 seconds (crontab scheduled)
**Cross-service impact:** Counter value is used by other services to detect device liveness; epoch tracking drives log rotation decisions.

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_kam_1219` | `tests/keep_alive_manager/test_tc_kam_1219_update_current_epoch_time.py` | Epoch updates every ~60 seconds (58-62s interval) | — |

---

### Flow 2: Log Rotation

**What happens:** On each invocation, KAM reads `bagheeraLogRotationTime` from `logconfig.ini` and compares it against `currentEpoch`. If the difference exceeds `LOG_ROTATION_CHECK_INTERVAL_SECS` (3600 seconds), KAM enters `set_config_to_path`, updates the `logrotationbagheera-timestamp` in `logconfig.ini` to the current epoch, and triggers log rotation. This causes service log files to be rotated (new log files created).

**When active:** Always
**Frequency:** Once every ~3600 seconds (1 hour), checked every minute
**Cross-service impact:** Rotates log files for all services (ndcentral, circ_buff, health, inference, etc.)

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_kam_1163` | `tests/keep_alive_manager/test_tc_kam_1163_log_rotation_interval.py` | LOG_ROTATION_CHECK_INTERVAL_SECS is 3600 | — |

---

### Flow 3: Keepalive API Call

**What happens:** Every ~10 minutes (controlled by `obsCallFreq` and `eaCallFreq`, default 10), KAM performs a keepalive wget API call to the cloud endpoint (`https://idms.netradyne.com/restserver/api/v1/keep-alive/<device_type>/<device_id>/<version>/`). The call includes JWT authentication headers. The response determines whether the device should upload logs (`'msg': 'upload-logs'`).

**When active:** Always
**Frequency:** Every ~10 minutes (every 10th KAM invocation)
**Cross-service impact:** Response triggers log upload pipeline (Flow 4)

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_kam_1392` | `tests/keep_alive_manager/test_tc_kam_1392_keepalive_api_call_every_ten_mins.py` | API call triggered every ~10 minutes | `BG4-517` |

---

### Flow 4: Log Upload Pipeline

**What happens:** When the keepalive API response contains `'msg': 'upload-logs'`, KAM enters `send_log_upload_message` → zips critical logs into a `.7z` archive → zips critical additional shield logs → marks "Done zipping logs" → sends upload message to uploader service → deletes the source log files that were compressed. The zip is stored in `/home/ubuntu/.nddevice/log/archive/critical/`.

**When active:** Always (triggered by API response)
**Frequency:** Every ~10 minutes (when API says upload-logs)
**Cross-service impact:** Sends upload request to `unifieduploader`; deletes compressed log files from disk

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_kam_1388` | `tests/keep_alive_manager/test_tc_kam_1388_device_logs_uploaded.py` | Full pipeline: enter → zip critical → zip shield → done → response | `OCTO-2190`, `BG4-517` |
| `TC_kam_1400` | `tests/keep_alive_manager/test_tc_kam_1400_keepalive_logupload_managed_by_separate_process.py` | KAM runs from crontab, uploader is separate process | `OCTO-2190` |

---

### Flow 5: Observations & EA Image Upload

**What happens:** Every ~10 minutes (controlled by `obsCallFreq` and `eaCallFreq`), KAM enters `check_and_upload_observations` to trigger upload of observation data from `/data/nd_files/nd_sdcard/observations`, then enters `check_and_upload_ea_images` to trigger EA (Event Analysis) image upload. After both, it enters `keep_alive_logs_upload` for the log upload pipeline. Finally, `send_misc_upload_message` handles miscellaneous upload requests.

**When active:** Always
**Frequency:** Every ~10 minutes
**Cross-service impact:** Triggers unified uploader to process observation and EA image files

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_kam_1388` | `tests/keep_alive_manager/test_tc_kam_1388_device_logs_uploaded.py` | Upload pipeline including observation check | `OCTO-2190`, `BG4-517` |

---

### Flow 6: Syslog Archival & Health Reporting

**What happens:** When `enable_syslog = true` in `[log]` config, KAM manages syslog files every ~10 minutes: detects rotated `syslog.1.gz` files → renames to `<epoch>_syslog.gz` → moves to `/var/log/nd_archive/syslog/` → inserts log info into uploader DB (`insert_log_info`) → sends syslog size data to health service. The `syslogSizeLimit` config (default 200 MB) determines the maximum total archived syslog size.

**When active:** Only when `[log] enable_syslog = true` in bagheera_override.ini
**Frequency:** Every ~10 minutes (same cycle as keepalive API)
**Cross-service impact:** Reports to `health` service; triggers `unifieduploader` for syslog upload

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_kam_2136` | `tests/keep_alive_manager/test_tc_kam_2136_enable_syslog_check_unifieduploader.py` | Uploader logs "syslog_enabled: true" after config enable | — |
| `TC_kam_2140` | `tests/keep_alive_manager/test_tc_kam_2140_files_moved_to_archive_path.py` | Syslog.gz moved to /var/log/nd_archive/syslog/ | `BG4-881` |
| `TC_kam_2142` | `tests/keep_alive_manager/test_tc_kam_2142_check_syslog_files_available.py` | Syslog and syslog.gz files exist in /var/log | `BG4-881` |
| `TC_kam_2143` | `tests/keep_alive_manager/test_tc_kam_2143_check_syslog_path_created.py` | /var/log/nd_archive/syslog/ directory created | — |
| `TC_kam_2160` | `tests/keep_alive_manager/test_tc_kam_2160_syslog_to_health_every_10_mins.py` | Syslog sent to health service every ~10 min interval | — |

---

### Flow 7: SVC Cleanup Syslog Preservation (25MB Threshold)

**What happens:** When SVC triggers disk cleanup (free space < 500MB), syslog files are subject to cleanup. KAM's syslog preservation logic ensures: if total syslog .gz files across `/var/log/*.gz` and `/var/log/nd_archive/syslog/*.gz` exceed 25MB, files are cleaned down to ~25MB. If total is already under 25MB, files are NOT deleted. This protects recent syslog data from aggressive disk cleanup.

**When active:** Only when `[log] enable_syslog = true` AND disk space drops below 500MB
**Frequency:** On-demand (triggered by SVC disk cleanup)
**Cross-service impact:** Depends on `svc` service for cleanup trigger; affects disk space availability

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_kam_2165` | `tests/keep_alive_manager/test_tc_kam_2165_svc_cleanup_25mb_preserved.py` | After cleanup, ~25MB preserved in /var/log/nd_archive/syslog | `BG4-881`, `DT-3802` |
| `TC_kam_2221` | `tests/keep_alive_manager/test_tc_kam_2221_syslog_not_deleted_if_less_than_25mb.py` | Files NOT deleted when total < 25MB | `DT-3802` |
| `TC_kam_2224` | `tests/keep_alive_manager/test_tc_kam_2224_svc_cleanup_25mb_preserved_both_paths.py` | 25MB preserved across both /var/log and /var/log/nd_archive/syslog | `DT-3802` |
| `TC_kam_2225` | `tests/keep_alive_manager/test_tc_kam_2225_files_still_available_both_paths.py` | Files still available in both paths when total < 25MB | — |

---

## Config-Driven Flow Activation

The agent MUST read the device config from `device_data/device_<ID>_config.ini`
before selecting test cases. Use the following mapping:

| Config Section | Config Key | Value | Activates Flow(s) | Test Cases Affected |
|---|---|---|---|---|
| — | — | — | Keepalive Counter & Epoch Tracking (always active) | `TC_kam_1219` |
| — | — | — | Log Rotation (always active) | `TC_kam_1163` |
| — | — | — | Keepalive API Call (always active) | `TC_kam_1392` |
| — | — | — | Log Upload Pipeline (always active) | `TC_kam_1388`, `TC_kam_1400` |
| — | — | — | Observations & EA Upload (always active) | `TC_kam_1388` |
| `[log]` | `enable_syslog` | `true` | Syslog Archival & Health Reporting | `TC_kam_2136`, `TC_kam_2140`, `TC_kam_2142`, `TC_kam_2143`, `TC_kam_2160` |
| `[log]` | `enable_syslog` | `true` | SVC Cleanup Syslog Preservation | `TC_kam_2165`, `TC_kam_2221`, `TC_kam_2224`, `TC_kam_2225` |

**Rules:**
- Flows marked "always active" → run their test cases unconditionally
- Flows gated by `[log] enable_syslog = true` → run only if the device config has that key set
- If `enable_syslog` is missing from the device config → default is `false` (skip syslog test cases)
- `syslog_limit` (default 2 MB) controls when syslog rotation triggers; tests set this via pre_steps

---

## Cross-Service Dependencies

| Related Service | Why | When to check its logs |
|---|---|---|
| `unifieduploader` | Receives upload requests from KAM; logs "syslog_enabled: true" when syslog active | When validating Syslog Archival (Flow 6), Log Upload (Flow 4) |
| `health` | Receives syslog size data; logs "primaryKey: health_info:disk_usage:syslog" | When validating Syslog Health Reporting (Flow 6, TC_kam_2160) |
| `svc` | Triggers disk cleanup when free space < 500MB; logs "CleanUP() freed" | When validating SVC Cleanup Preservation (Flow 7) |
| `uploader` | Runs as separate process; uploads .7z archives to cloud | When validating Log Upload (Flow 4, TC_kam_1400) |

---

## Flow Dependency Graph

```
crontab (every 1 min) → [Flow 1: Counter & Epoch Tracking] → write keepalive_count.txt
                       → [Flow 2: Log Rotation Check] → (if epoch - lastRotation > 3600) → set_config_to_path → rotate logs
                       → (every 10th invocation) → [Flow 5: Observations & EA Upload]
                                                  → [Flow 6: Syslog Archival] (only if enable_syslog=true)
                                                  → [Flow 3: Keepalive API Call] → (if response='upload-logs') → [Flow 4: Log Upload Pipeline]
disk space < 500MB → SVC cleanup → [Flow 7: Syslog Preservation] (only if enable_syslog=true)
```

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Determine active flows** using the Config-Driven Flow Activation table above
3. **For each active flow**, read the mapped `.py` test case files from `tests/keep_alive_manager/`
4. **From each test file**, use the acceptance criteria and log patterns defined in the test functions
5. **Search device logs** in `device_logs/<device_id>/` using patterns from the test files and Key Log Patterns above
6. **For cross-service checks**, also search logs of related services listed above
7. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED
