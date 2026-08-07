---
name: circbuff-service-validation
description: "Use when: validating Circular Buffer (circular_buffer) service behavior from device logs. Covers initialization, DB management, SD card storage, file rotation/deletion, transcoding, cloud notification, SD card recovery, DRP enforcement, and root filesystem monitoring."
argument-hint: "device ID (e.g., /circbuff-service-validation 103452403525)"
---

# Circular Buffer (`circular_buffer`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the circular_buffer service —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads test case files for actual log patterns,
> device-type paths, and acceptance criteria — this skill does NOT duplicate those.

---

## Service Overview

`circular_buffer` is a critical always-running service that manages the complete lifecycle of video files on a Netradyne device — from creation through transcoding, SD card storage, cloud notification, and eventual rotation/deletion.
It handles database tracking of all video files (SQLite VIDFILES table), SD card space management with fill-limit enforcement, transcoding of HQ videos to LD format, alert file retention, DRP (Data Retention Policy) enforcement, and SD card recovery on unmount/corruption events.
The service interacts with `uploader`, `power_monitor`, `nd_central`, `service_mon`, and `cam_rec` via POSIX message queues (`/dev/shm/MSGQ/`).

**Process name:** `circular_buffer`
**Log file:** `circ_buff` logs in `log_<epoch_ms>.log` format (path defined per device type)
**Log folders:** `/home/ubuntu/.nddevice/log/circ_buff/` (non-critical), `/home/ubuntu/.nddevice/log/circ_buff_c/` (critical)
**Primary config sections:** `[Transcode]`, `[sdcard]`, `[drp]`, `[log]`, `[do_not_transcode]`, `[dms_camera]`, `[ext_cam_settings]`

---

## Device-Type-Specific Paths

| Resource | krait / krait2 | bagheera2 / bagheera3 / bagheera4 / octo |
|----------|---------------|---------------------------------------------|
| DB path | `/home/ubuntu/.nddevice/db/circular_buffer.db` | `/home/ubuntu/.nddevice/circular_buffer.db` |
| SD card mount path | `/data/nd_files/nd_sdcard/` | `/media/data/nd_sdcard/` |
| SD card physical mount | `/data` | `/media/data` |
| Config path | `/data/nd_files/config` | `/home/ubuntu/config` |
| Log root | `/data/nd_files/log` | `/home/ubuntu/.nddevice/log` |
| Log symlink | Present (log folder) | Not applicable |
| File ownership | `root root` | `ubuntu ubuntu` |

---

## Service Flows

### Flow 1: Service Initialization

**What happens:** On boot, the service initializes the logger, parses the override config (`bagheera_override.ini`), creates the message queue (`q_circular_buffer`), opens the SQLite DB, creates/verifies tables (VIDFILES, DB_DETAILS), adds any missing columns (UDID, SESSIONCOUNT, UPL_VID_ENABLED, REC_VID_ENABLED), fills the circular buffer header with device identity, and reads all config sections. Spawns threads: cloud_notifier, storage_monitor (transcode), sdcard_recovery, uploader_query, sysv_messageq, send_storage_info.

**When active:** Always — runs once at every service start/restart
**Frequency:** Once at boot or service restart
**Cross-service impact:** Sends queue name to nd_central; depends on power_monitor for keepalive

**Key log patterns:**
- `#####Starting Circular_Buffer#####` — service startup confirmation (CRITICAL level)
- `OVerride file parsed successfully` — config override loaded
- `Message queue server created q_circular_buffer` — MSGQ ready
- `Success in db_main_msg_q` — DB message queue operational
- `SQL error@ table DB_DETAILS already exists` / `Ignoring error since table already exists` — normal on restart
- `success in create_table_db` — DB tables verified
- `success in open DB` — DB accessible
- `success mutex init` — thread safety ready

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_607` | `tests/circbuff/test_tc_circbuff_607_activity.py` | Service is running (systemctl active) |
| `TC_609` | `tests/circbuff/test_tc_circbuff_609_override_parsing_status.py` | Override config parsed log present |
| `TC_610` | `tests/circbuff/test_tc_circbuff_610_msgq_creation.py` | MSGQ creation log + /dev/shm/MSGQ file |
| `TC_663` | `tests/circbuff/test_tc_circbuff_663_log_verification.py` | Starting log, folders, permissions, ownership |
| `TC_758` | `tests/circbuff/test_tc_circbuff_758_config_pushed_parsed.py` | Config parsed from main config file |

---

### Flow 2: Database Management & Cleanup

**What happens:** After initialization, the main message loop receives `REQ_CIRCULAR_BUFFER_CLEAN_DB`, triggering `get_details()` to scan all files on disk and in DB, then `circular_buffer_cleanup()` to reconcile differences — removing DB entries for files missing from disk and vice versa. Also calls `adjust_filling_limit()` to recalculate storage limits. Checks for NULL rows and reports them.

**When active:** Always — once after boot, then on specific trigger messages
**Frequency:** Once at startup, plus on-demand
**Cross-service impact:** None directly; keeps DB consistent for uploader queries

**Key log patterns:**
- `CLEAN_DB received` — cleanup triggered
- `total files found in DIR <N>` — files counted on disk
- `total files found in DB <N>` — files counted in database
- `circular_buffer_cleanup: clean_memorycard` — disk cleanup phase
- `circular_buffer_cleanup: after clean_memorycard: clean_db` — DB cleanup phase
- `card stats: calling adjust_filling_limit` — fill limit recalculation
- `NULL value found in CB DB, total null_row_cnt: <N>` — corrupted entries

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_611` | `tests/circbuff/test_tc_circbuff_611_db_availability.py` | DB table creation logs present |
| `TC_685` | `tests/circbuff/test_tc_circbuff_685_db_file_permissions.py` | DB file ownership is root:root |
| `TC_686` | `tests/circbuff/test_tc_circbuff_686_db_path_verification.py` | DB file exists at correct path |
| `TC_687` | `tests/circbuff/test_tc_circbuff_687_db_field_validation.py` | All required columns present in VIDFILES |

---

### Flow 3: SD Card Stats & Fill Limit

**What happens:** The service reads SD card capacity, used, and available space via `statvfs`. Calculates `FILLING_LIMIT` based on storage tier and card capacity, ensuring total_backup_space (5 sessions worth) is reserved. Logs card stats periodically. If fill limit is exceeded, triggers file deletion.

**When active:** Always
**Frequency:** Every ~30s during storage_monitor loop, and on each file addition
**Cross-service impact:** When space is critically low, may trigger reboot via power_monitor

**Key log patterns:**
- `card_capacity <N>, card_used <N>, card_avilable <N>` — raw SD stats
- `card_capacity_gb <N>` — capacity in GB
- `card_capacity <N> FILLING_LIMIT <N> FILLING_LIMIT(GB) <F> RESERVED_SPACE <N> MAX_EMMC_FREE_SPACE(used for range check) <N> total_backup_space <N>` — full stats (CRITICAL level)
- `FILLING_LIMIT less than total backup space` — critical space error
- `Free space in eMMC = <N> bytes, total backup space = <N> bytes` — periodic space check

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_678` | `tests/circbuff/test_tc_circbuff_678_sdcard_stats.py` | SD card stats logged after restart |
| `TC_680` | `tests/circbuff/test_tc_circbuff_680_sdcard_fill_percentage.py` | Fill percentage within expected range |
| `TC_777` | `tests/circbuff/test_tc_circbuff_777_storage_hours_check.py` | Storage hours >= expected for tier |
| `TC_2801` | `tests/circbuff/test_tc_circbuff_2801_validate_card_filling_limit_across_reboots.py` | Fill limit consistent across reboots |

---

### Flow 4: File Addition & Tracking

**What happens:** When cam_rec or other services produce a video file, they send `REQ_CIRCULAR_BUFFER_ADD_FILE_DB` to the message queue. The service validates the filename, checks if file already exists on SD card (bagheera), adds the file record to DB with metadata (time, duration, size, type, status, cam_type, transcode_status, compression, udid, sessionCount). Also handles `.zip` alert files and `.aac` audio files.

**When active:** Always — whenever new files are recorded
**Frequency:** Every ~60s (one per recording session per camera)
**Cross-service impact:** Receives messages from cam_rec, nd_sam; triggers cloud_notifier to report new files

**Key log patterns:**
- `ADD_FILE_DB: <filename> file.time: <epoch>` — file being added
- `Success in add_file_DB` — file added successfully
- `REQ_CIRCULAR_BUFFER_ADD_FILE_DB_PYTHON received` — alert/zip file from Python service
- `File already available in SdCard, ignoring this request` — duplicate prevention
- `Oldest index: <N>, Newest index: <N>, Count: <N>` — session range stats

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_688` | `tests/circbuff/test_tc_circbuff_688_file_copy_status.py` | Files copied from /home/iriscli/files to SD card |
| `TC_697` | `tests/circbuff/test_tc_circbuff_697_partial_file_addition.py` | Partial file entries after mid-recording reboot |
| `TC_766` | `tests/circbuff/test_tc_circbuff_766_alert_session_check_sdcard.py` | Alert files retained on SD |
| `TC_796` | `tests/circbuff/test_tc_circbuff_796_file_type_verification.py` | File TYPE values correct (3=video, 6=audio) |

---

### Flow 5: Transcoding (Storage Monitor Thread)

**What happens:** The `storage_monitor_main` thread (tagged `TC_T`) reads transcode config from `bagheera_config.ini` (num_hq_videos_max, enable, delay, enable_smart_transcode, enable_inward_transcode). After a configurable delay (`TRANSCODE_START_DELAY`), it enters a loop: checks mount status, checks free space, gets number of HQ files per camera, and transcodes files from HQ to LD when HQ count exceeds `num_hq_videos_max`. After transcoding, deletes the original HQ file. Also publishes oldest uploadable file info.

**When active:** Always (if `[Transcode] enable = true`)
**Frequency:** Every 60s check cycle; transcode runs when HQ files exceed max
**Cross-service impact:** Publishes `OLDEST_UPLOADABLE_FILE` topic for uploader service

**Key log patterns:**
- `DB sanitized. Ready for transcoding.` — transcode thread initialized
- `Reading /home/ubuntu/.nddevice/latest/bagheera_config.ini` — config read
- `Obtained num_hq_videos_max = <N> from <path> file` — HQ limit parsed
- `Obtained enable = true from <path> file` — transcoding enabled
- `Transcoding will start after <N>s` — delay before start
- `<CAMERA>, TRANSCODE WAITING, NUMFILES = <N>` — pending count per camera
- `<CAMERA>, TRANSCODED, NUMFILES = <N>` — completed count per camera
- `sync_directories() completed. Starting TC` — transcode active
- `Num HQ <CAMERA> files in db <N>, NUM_HQ_VIDEOS_MAX = <N>` — rotation check
- `Running transcode for <filename>` — active transcode
- `Transcode Success : <filename>, fc = <compression>, time = <N>ms` — completed
- `Transcoding disabled. Checking after <N> seconds` — TC disabled
- `SDCARD UNMOUNTED: Transcoding stopped` — blocked by mount issue

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_684` | `tests/circbuff/test_tc_circbuff_684_transcode_status.py` | Transcode WAITING/TRANSCODED logged per camera |
| `TC_727` | `tests/circbuff/test_tc_circbuff_727_transcode_file_size.py` | Transcoded file sizes within limits |
| `TC_728` | `tests/circbuff/test_tc_circbuff_728_max_hq_parse.py` | Default num_hq_videos_max when key removed |
| `TC_765` | `tests/circbuff/test_tc_circbuff_765_max_hq_files.py` | HQ count stays within max limit |
| `TC_790` | `tests/circbuff/test_tc_circbuff_790_transcode_compression_normal.py` | Normal file compression type |
| `TC_791` | `tests/circbuff/test_tc_circbuff_791_transcode_compression_alert.py` | Alert file compression type |
| `TC_792` | `tests/circbuff/test_tc_circbuff_792_transcode_compression_partial.py` | Partial file compression type |
| `TC_797` | `tests/circbuff/test_tc_circbuff_797_transcode_compression_multiple_privacy.py` | Privacy mode transitions compression |

---

### Flow 6: File Rotation & Deletion

**What happens:** When the SD card approaches `FILLING_LIMIT`, the service deletes the oldest files (FIFO order by session) to make room for new recordings. The `add_file_normal()` function checks if adding a new file would exceed the limit and triggers deletion of the oldest session. Files are deleted from disk and removed from DB. Also handles force-delete when free space drops below threshold percentage of backup space.

**When active:** Always — triggered when space is low
**Frequency:** On every file addition when space is near limit
**Cross-service impact:** Uploader must upload files before they age out

**Key log patterns:**
- `Deleted <path>. Time taken for delete = <N>ms` — file removed from disk
- `SQL DELETE FROM VIDFILES WHERE NAME == '<filename>'` — DB entry removed
- `Files deleted : <N>` — deletion count per cycle
- `Below is the deleted file list` — deletion batch start
- `Video list to notify cloud: Number of files added = <N>, Number of files deleted = <N>` — cloud sync
- `Free space <N> bytes less than <N> backup space bytes. call add_file_normal` — force cleanup
- `Forcefully deleting oldest session` — emergency space reclaim

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_848` | `tests/circbuff/test_tc_circbuff_848_file_rotation.py` | File rotation activity in logs (oldest deleted) |
| `TC_873` | `tests/circbuff/test_tc_circbuff_873_zero_mb_file_deletion.py` | Zero-size files deleted from SD |
| `TC_874` | `tests/circbuff/test_tc_circbuff_874_improper_name_format_deletion.py` | Invalid filename files removed |
| `TC_879` | `tests/circbuff/test_tc_circbuff_879_removed_file_deletion.py` | Deleted files removed from DB |

---

### Flow 7: Cloud Notification

**What happens:** The `cloud_notifier_main` thread sends the video list (add/delete operations since last notification) to the cloud via HTTPS POST to `<server_url>/upload/videolist` every `NOTIFY_DURATION` minutes (default 5 min). Retries on failure up to `MAX_CLOUD_NOTIFY_RETRY` times. Also triggers log rotation (`route_logs`) periodically.

**When active:** Always
**Frequency:** Every 5 minutes (configurable via `NOTIFY_DURATION`)
**Cross-service impact:** Cloud service receives file inventory; uploader depends on cloud acknowledgment

**Key log patterns:**
- `URL @@ https://idms.netradyne.com/restserver/api/v1/upload/videolist` — API call
- `res 0 , CURLE_OK 0` — HTTP success
- `string @@{"response":true,"msg":"Videolist saved."}@@` — cloud acknowledged
- `collect_notify_videolist is succeded` — full cycle success
- `send_video_list failed` — notification failure
- `Video list to notify cloud: Number of files added = <N>, Number of files deleted = <N>` — payload summary

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_690` | `tests/circbuff/test_tc_circbuff_690_upload_api_frequency.py` | Upload videolist URL frequency (~5min) |
| `TC_691` | `tests/circbuff/test_tc_circbuff_691_add_list_frequency.py` | Add list notify frequency |
| `TC_692` | `tests/circbuff/test_tc_circbuff_692_del_list_frequency.py` | Delete list notify frequency |
| `TC_800` | `tests/circbuff/test_tc_circbuff_800_api_call_no_network.py` | Queue behavior when network unavailable |

---

### Flow 8: SD Card Recovery

**What happens:** The `sdcard_recovery_thread_main` thread (tagged `SD_REC`) monitors SD card health. Periodically checks mount status, filesystem attributes (immutable flag), and performs recovery operations if the card becomes unmounted or read-only. If SD card is unmounted for >20 minutes, triggers reboot via power_monitor. Recovery stages include unmount, fsck, remount, and attribute check.

**When active:** Always
**Frequency:** Every 30s health check cycle
**Cross-service impact:** Triggers reboot via power_monitor on prolonged SD failure; blocks transcoding while unmounted

**Key log patterns:**
- `sdcard_recovery_thread_main initialized.` — thread started
- `Entered notify_sdcard_recovery` — periodic check
- `recovery_result <N> current_monotonic_time <N> last_sdcard_recovery_monotonic_time <N>` — health status
- `Entered sdcard_recovery_check` / `Exited sdcard_recovery_check` — recovery cycle
- `findmnt | grep /media/data returned empty. Looks like SD card got unmounted` — unmount detected
- `mount service enable cmd: systemctl enable media-data.mount` — remount attempt
- `umount_sdcard() Entered` — unmount for recovery

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_683` | `tests/circbuff/test_tc_circbuff_683_sdcard_mount_partition.py` | SD card mounted correctly, source device correct |
| `TC_701` | `tests/circbuff/test_tc_circbuff_701_sdcard_remount_check.py` | SD auto-remount after forced unmount |

---

### Flow 9: DRP (Data Retention Policy)

**What happens:** When `[drp] enabled = 1`, the storage_monitor thread calls `delete_old_files_out_of_drp()` every cycle. This deletes video files older than `drp_clock_hours` (default 72h). Two deletion methods: `delete_n_old_session_drp()` deletes sessions older than threshold, `delete_clock_hours_drp()` enforces the clock-hour window.

**When active:** Only when `[drp] enabled = 1` in bagheera_config.ini
**Frequency:** Every 60s during storage_monitor loop
**Cross-service impact:** Affects uploader — files may be deleted before upload if DRP window is short

**Key log patterns:**
- `drp_config_str: <val> drp_enabled: <0|1>` — DRP config status
- `drp_config_str: <val> , clock_hours: <N>` — retention window
- `drp is enabled. calling delete_old_files_out_of_drp()` — DRP active
- `Total video file deleted in delete_n_old_session_drp() : <N>` — session deletion count
- `Total video file deleted in delete_clock_hours_drp() : <N>` — clock-hour deletion count

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_1205` | `tests/circbuff/test_tc_circbuff_1205_drp_vid_files_validation.py` | No video file beyond DRP window |
| `TC_1265` | `tests/circbuff/test_tc_circbuff_1265_drp_obs_files_validation.py` | No zip/obs file beyond DRP window |
| `TC_1266` | `tests/circbuff/test_tc_circbuff_1266_drp_minified_obs_files_validation.py` | No minified obs file beyond DRP |
| `TC_1267` | `tests/circbuff/test_tc_circbuff_1267_drp_audio_files_validation.py` | No audio file beyond DRP window |
| `TC_1308` | `tests/circbuff/test_tc_circbuff_1308_drp_default_config_parameter_validation.py` | Default DRP params valid |

---

### Flow 10: Stability & Power Events

**What happens:** The service must survive and recover from: low-power wakeup cycles, abrupt power-off, AWS-triggered reboots, cyclic reboots, and camera crashes. On restart after any of these events, checks for the reboot token file (`circ_buff_reboot_token_file.bin`) to detect crash vs clean boot. Sends `REQ_POWERMON_SVC_TO_REBOOT` to power_monitor when keepalive reboot is needed.

**When active:** Always — validation occurs after power events
**Frequency:** On each boot/restart
**Cross-service impact:** Depends on power_monitor for reboot; service_mon monitors health

**Key log patterns:**
- `<token_file> is already present. circular_buffer must have a crash and start` — crash detected
- `REQ_POWERMON_TO_SVC_REBOOT recieved` — keepalive reboot trigger
- `send_low_power_wakeup_count_time_sync() m.low_power_wakeup_cnt: <N>` — LPW count

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_613` | `tests/circbuff/test_tc_circbuff_613_status_in_lpw.py` | Service starts after LPW, power_mon sends count |
| `TC_665` | `tests/circbuff/test_tc_circbuff_665_stability_lpw.py` | Service uptime >= 3 min after LPW cycle |
| `TC_666` | `tests/circbuff/test_tc_circbuff_666_stability_abrupt_poweroff.py` | Service uptime >= 3 min after power cut |
| `TC_669` | `tests/circbuff/test_tc_circbuff_669_stability_aws_reboot.py` | Service uptime >= 3 min after AWS reboot |
| `TC_672` | `tests/circbuff/test_tc_circbuff_672_stability_cyclic_reboot.py` | Service stable after cyclic reboot |
| `TC_674` | `tests/circbuff/test_tc_circbuff_674_stability_camera_crash.py` | Service uptime >= 2 min after cam crash |

---

### Flow 11: Log Management (Critical/Non-Critical Zip)

**What happens:** The service's logging infrastructure (via `route_logs()`) manages log rotation and archival. Log files are named `log_<epoch_ms>.log`. Critical logs go to `circ_buff_c/`, non-critical to `circ_buff/`. When `[log] enable_non_critical = false`, non-critical logs are disabled. Log files are periodically archived into `.zip` or `.7z` format.

**When active:** Always for critical; non-critical controlled by config
**Frequency:** Log rotation triggered by cloud_notifier thread periodically
**Cross-service impact:** None — internal log management

**Key log patterns:**
- `Zipping critical logs` — critical log archival
- `Going for 7z` — 7z compression started
- Log files named: `log_<epoch_ms>.log` in both `circ_buff/` and `circ_buff_c/`

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_737` | `tests/circbuff/test_tc_circbuff_737_non_critical_enable.py` | Both folders exist when enable_non_critical=true |
| `TC_738` | `tests/circbuff/test_tc_circbuff_738_log_zip_noncritical_folder.py` | Non-critical logs zipped |
| `TC_739` | `tests/circbuff/test_tc_circbuff_739_c_log_zip_critical_folder.py` | Critical logs zipped |

---

### Flow 12: Root Filesystem Monitoring

**What happens:** On devices that support readonly repair (`nd_factory_utils::is_readonly_repair_supported()`), reads root filesystem monitor config. If enabled, runs `root_filesystem_status_check_repair()` to detect and fix read-only root filesystem issues. Can trigger fsck on low-power wakeup if configured.

**When active:** Only on supported devices with config enabled
**Frequency:** Once at boot
**Cross-service impact:** May trigger device reboot for filesystem repair

**Key log patterns:**
- `Reading root filesystem monitoring configuration...` — config read
- `Root filesystem monitoring configuration loaded successfully` — enabled
- `Root filesystem monitoring is disabled or config failed to load` — disabled

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| — | — | No dedicated test case currently |

---

## Config-Driven Flow Activation

The agent MUST read the device config before selecting test cases. Use the following mapping:

| Config Section | Config Key | Value | Activates Flow(s) | Test Cases Affected |
|---|---|---|---|---|
| `[Transcode]` | `enable` | `true` | Transcoding (Flow 5) | `TC_684`, `TC_727`, `TC_728`, `TC_765`, `TC_790-792`, `TC_797` |
| `[Transcode]` | `num_hq_videos_max` | `<int>` (default 120) | HQ file rotation threshold | `TC_728`, `TC_765` |
| `[Transcode]` | `enable_smart_transcode` | `true` | Smart transcode logic | `TC_790`, `TC_791` |
| `[Transcode]` | `enable_inward_transcode` | `true` | Inward camera transcoding | `TC_684` |
| `[drp]` | `enabled` | `1` | DRP Enforcement (Flow 9) | `TC_1205`, `TC_1265`, `TC_1266`, `TC_1267`, `TC_1308` |
| `[drp]` | `clock_hours` | `<int>` (default 72) | DRP retention window | `TC_1205`, `TC_1265-1267` |
| `[log]` | `enable_non_critical` | `true`/`false` | Non-critical log folder (Flow 11) | `TC_737`, `TC_738`, `TC_739` |
| `[sdcard]` | `mount` | `true`/`false` | SD card mount behavior | `TC_683`, `TC_701` |
| `[sdcard]` | `use_unlink` | `true`/`false` | Unlink vs remove for deletion | `TC_848`, `TC_873` |
| `[do_not_transcode]` | `limit_alert_HD_count` | `<int>` (default 80) | Alert HQ file limit | `TC_766` |
| `[dms_camera]` | `enabled` | `true`/`false` | DMS camera file tracking | — |
| — | — | — | Init, DB, SD stats, Cloud Notify (always active) | `TC_607-611`, `TC_663`, `TC_678`, `TC_680`, `TC_690-692` |

**Rules:**
- Flows marked "always active" → run their test cases unconditionally
- Flows gated by a config key → run only if the device config has that key set to the activating value
- If the config key is missing from the device config → use the default value listed above

---

## Cross-Service Dependencies

| Related Service | Why | When to check its logs |
|---|---|---|
| `service_mon` | Monitors circular_buffer health; receives error codes | When validating Flow 1 (init failures), Flow 10 (stability) |
| `power_monitor` | Receives reboot requests from CB; manages keepalive | When validating Flow 8 (SD recovery reboot), Flow 10 (stability) |
| `uploader` | Queries CB for uploadable files; receives OLDEST_UPLOADABLE_FILE | When validating Flow 7 (cloud notify), Flow 9 (DRP) |
| `cam_rec` | Sends ADD_FILE_DB messages when recordings complete | When validating Flow 4 (file addition) |
| `nd_central` | CB registers its queue name with nd_central | When validating Flow 1 (init) |

---

## Flow Dependency Graph

```
boot → [Flow 1: Init] → logger + config + DB + MSGQ + threads
                      → [Flow 2: DB Cleanup] → CLEAN_DB + sync_directories
                      → [Flow 3: SD Card Stats] → card_stats() + FILLING_LIMIT
                      → [Flow 5: Transcoding] (waits for sync_directories)
                      → [Flow 7: Cloud Notify] (sleeps 15s then starts)
                      → [Flow 8: SD Recovery] (thread runs independently)

message (ADD_FILE_DB) → [Flow 4: File Addition] → DB insert
                      → [Flow 6: File Rotation] (when FILLING_LIMIT reached)
                      → [Flow 7: Cloud Notify] (batches adds/deletes)

config ([drp] enabled=1) → [Flow 9: DRP] (runs inside storage_monitor loop)

event (SD unmount >20min) → [Flow 8: SD Recovery] → reboot via power_monitor
event (power off/LPW/crash) → [Flow 10: Stability] → service restart → Flow 1
config ([log] enable_non_critical) → [Flow 11: Log Management]
```

---

## Log Format

All circ_buff logs use epoch-millisecond format:
```
<epoch_ms>: <uptime_ms>: <TAG>: <LEVEL>: <PID>: <TID>: <message>
```

Example:
```
1779371787007: 87: CB: C: 6634: 6634: #####Starting Circular_Buffer#####
1779371812968: 26048: TC_T: I: 6634: 7636: Obtained num_hq_videos_max = 120 from /home/ubuntu/.nddevice/latest/bagheera_config.ini file
1779371869596: 82676: FILE_U: I: 6634: 7636: Deleted /media/data/nd_sdcard/1_trip0c5a_part0158f0.mp4. Time taken for delete = 11ms
```

**Tags:** `CB` (main), `TC_T` (transcode thread), `SD_REC` (sdcard recovery), `CB_SQL` (SQL operations), `CB_VID` (video class), `BUF_CU` (buffer common utils), `FILE_U` (file utils), `CFG_PRSR` (config parser), `MSGQ` (message queue), `NDMBS` (message bus server)

**Levels:** `C` (Critical), `E` (Error), `W` (Warning), `I` (Info), `D` (Debug)

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Determine active flows** using the Config-Driven Flow Activation table above
3. **For each active flow**, read the mapped test case files from `tests/circbuff/`
4. **From each test**, use assertion patterns and shell commands for log searches
5. **Search device logs** in `device_logs/<device_id>/circ_buff.log` using patterns from this skill
6. **For cross-service checks**, also search logs of related services listed above
7. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED
8. **Time filtering**: Use device boot epoch to filter logs from current session only
9. **Multi-tag awareness**: A single log file contains entries from multiple tags (CB, TC_T, SD_REC, etc.) — filter by tag when checking specific flows
