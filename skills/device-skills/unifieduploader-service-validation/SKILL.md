---
name: unifieduploader-service-validation
description: "Use when: validating Unified Uploader (unifieduploader) service behavior from device logs. Covers service startup & initialization, event/alert upload, VOD upload (internal + external camera), observations upload, critical/non-critical log upload, syslog upload, LLA (Low Latency Alert) upload, DRP (Data Retention Policy), EA images upload, sign crops upload, VOD elapsed time tracking, CB broadcast/DRP cleanup, pending VOD status reporting, and health stats signal info."
argument-hint: "device ID (e.g., /unifieduploader-service-validation 440073)"
---

# Unified Uploader (`unifieduploader`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the Unified Uploader service —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads test cases for actual log patterns,
> device-type paths, and acceptance criteria — this skill does NOT duplicate those.

---

## Service Overview

`unifieduploader` is a critical multi-threaded upload service that handles all data uploads from the device to the Netradyne cloud. It manages priority-queued uploads for alerts/events, Video-on-Demand (VOD), observations, device logs, sign crops, EA images, and Low Latency Alerts (LLA). The service receives upload requests via a POSIX message queue (`UniUpload`), persists them in a SQLite database for retry/reboot resilience, and uploads via HTTPS (libcurl) multipart form requests to cloud APIs.

It interacts with `awsiot` (receives VOD requests), `circular_buffer` (queries file availability + DRP status), `conn_mgr` (signal info), `power_monitor` (ignition events, pending VOD status), `health` (upload metrics), and `ext_cam` (external camera VOD fetching).

**Process name:** `unifieduploader`
**Log file:** `unifieduploader.log` (path defined per device type in device config)
**Primary config sections:** `[uploader_settings]`, `[log]`, `[drp]`, `[ea_config]`, `[sdcard]`, `[hdmaps_mode]`, `[low_latency_alert_notification]`

---

## Threading Model

The service spawns these threads at startup (all must appear in logs for healthy init):

| Thread Name Tag | Function | Purpose |
|-----------------|----------|---------|
| `PS` | `handle_req_upload` | Small payload uploads (events, observations) |
| `PL` | `handle_req_upload_long` | Large payload uploads (VODs) |
| `TH_LOG_UPL` | `handle_req_upload_misc` | Log uploads, sign crops, EA images, VOD list |
| `TH_UPLOADER_DATA_UPLOAD_PENDING_STATUS` | `uploader_data_upload_pending_status_thread` | Reports pending VOD count to power_monitor on IGN OFF |
| `TH_VOD_ELAPSED_TIME` | `handle_vod_elapsed_time` | Tracks VOD elapsed time + DRP timer |
| `PLLA` | `handle_req_upload_lla` | LLA message subscription + queue (only if LLA enabled) |
| `PLLA_U` | `upload_lla` | LLA upload worker (only if LLA enabled) |
| `TH_FETCH_EXT_VOD` | `handle_ext_vod` | External camera VOD fetching (created on-demand) |
| (HS thread) | `handle_upl_hs` | Health stats signal info collection |

---

## Service Flows

### Flow 1: Service Startup & Initialization

**What happens:** On boot, the service initializes logging, parses config files (cloudconfig.ini, nddevice.ini, deviceconfig.ini, bagheera_config.ini with override), reads config-driven settings (curl timeouts, DRP, syslog, LLA, EA), initializes CURL, creates the SQLite DB, creates message queues, sets up CB broadcast subscription, cleans up stale files, restores failed uploads from DB, creates all worker threads, and enters the main message loop.

**When active:** Always (every boot)
**Frequency:** Once per boot
**Cross-service impact:** Must complete before any uploads can proceed. CB broadcast subscription connects to circular_buffer service.

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| `TC_unifieduploader_464` | Service is active/running |
| `TC_unifieduploader_465` | Log folder created |
| `TC_unifieduploader_466` | SQLite DB file created |
| `TC_unifieduploader_827` | Message queue created |
| `TC_unifieduploader_556` | Override config parsed correctly |
| `TC_unifieduploader_832` | Default config values applied |
| `TC_unifieduploader_3490` | CURL global init at bootup |
| `TC_unifieduploader_719` | Uptime-dependent service check |
| `TC_unifieduploader_720` | Message queue dependent services |

---

### Flow 2: Event/Alert Upload

**What happens:** When an alert is generated (e.g., driver distraction, collision), ndcentral sends a `REQ_UPLOAD_EVENT` message. The uploader copies the alert JSON to the circular buffer, inserts a DB record, adds it to the small-payload priority queue, and uploads via the `/eventdata` API. On success, the DB record is deleted. Signal info is collected and sent to health stats for each alert session.

**When active:** Always
**Frequency:** On event (each alert trigger)
**Cross-service impact:** Interacts with circular_buffer (copy alert), health service (signal info), conn_mgr (signal strength data)

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| `TC_unifieduploader_601` | Event data upload call |
| `TC_unifieduploader_670` | Priority 1 for event data |
| `TC_unifieduploader_844` | Alert LD file upload |
| `TC_unifieduploader_597` | Signal info sent to HS |
| `TC_unifieduploader_2049` | User alert priority |

---

### Flow 3: VOD (Video-on-Demand) Upload

**What happens:** AWSIOT receives a cloud VOD request and forwards it as `REQ_UPLOAD_VOD` (PAYLOAD_LARGE). The uploader persists it in the DB, adds to the large-payload priority queue, dequeues by priority, checks file availability via circular_buffer, optionally decrypts/trims the video (ffmpeg), creates a tar, uploads via `/upload/video` API. On success, deletes DB entry and cleans up files. Supports cancellation, priority changes, retry with backoff, and timeout (configurable, default 72hrs).

**When active:** Always
**Frequency:** On demand (cloud-triggered VOD requests)
**Cross-service impact:** awsiot (receives request), circular_buffer (file availability, DRP check), power_monitor (pending count on IGN OFF)

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| `TC_unifieduploader_86` | Complete VOD upload pipeline |
| `TC_unifieduploader_2050` | VOD upload retry |
| `TC_unifieduploader_2052` | VOD priority 6 |
| `TC_unifieduploader_2082` | Priority ordering |
| `TC_unifieduploader_2496` | VOD retry count |
| `TC_unifieduploader_124` | VOD expiration (30 days) |
| `TC_unifieduploader_126` | VOD persistence across reboot |
| `TC_unifieduploader_2068` | VOD retry after reboot |
| `TC_unifieduploader_2099` | Pending VOD flow |
| `TC_unifieduploader_2116` | Pending VOD list CSV columns |
| `TC_unifieduploader_2117` | Pending VOD when offline |
| `TC_unifieduploader_2118` | Pending VOD after reboot |
| `TC_unifieduploader_2122` | CSV matches DB |
| `TC_unifieduploader_2197` | VOD without LTE |
| `TC_unifieduploader_2198` | VOD in low power mode |
| `TC_unifieduploader_2551` | 10 concurrent VOD requests |
| `TC_unifieduploader_2555` | VOD retry after service restart |
| `TC_unifieduploader_2545` | VOD when SD card file missing |
| `TC_unifieduploader_845` | Skip unavailable video |
| `TC_unifieduploader_2070` | VOD gap < 1.5s |
| `TC_unifieduploader_3486` | VOD file decryption |
| `TC_unifieduploader_3488` | VOD with changed config |
| `TC_unifieduploader_2580` | VOD reboot with reason 6 |

---

### Flow 4: VOD — Circular Buffer Interaction & Failures

**What happens:** Before uploading a VOD, the uploader queries circular_buffer for file availability and DRP status. If CB is unresponsive, the VOD is deprioritized with backoff. Various failure reasons are tracked: file unavailable, DRP blocked, privacy enabled, decryption failed, timeout, CB query failure, etc. Each failure is reported to VOD health stats.

**When active:** During any VOD upload attempt
**Frequency:** Per VOD request
**Cross-service impact:** circular_buffer (file query, DRP check)

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| `TC_unifieduploader_2561` | CB unresponsive handling |
| `TC_unifieduploader_2578` | CB unresponsive reason 6 |
| `TC_unifieduploader_2579` | CB crash handling |
| `TC_unifieduploader_2495` | VOD failure reason 6 |

---

### Flow 5: External Camera VOD

**What happens:** When a VOD request targets an external camera file, the uploader creates a dedicated `TH_FETCH_EXT_VOD` thread (on-demand, first ext VOD request). It sends a fetch request to the `ext_cam` service, waits for ACK/SUCCESS/FAILURE/NOT_AVAILABLE response, updates DB, and then adds the file to the upload queue. Supports RGB analysis status from ext_cam.

**When active:** Only when VOD request targets external camera video
**Frequency:** Per external camera VOD request
**Cross-service impact:** ext_cam service (fetch + status), circular_buffer (file storage)

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| (External VOD test cases are typically tested as part of full VOD flow TC_86 and TC_2551) | | |

---

### Flow 6: Observations Upload

**What happens:** ndcentral periodically triggers `REQ_UPLOAD_OBSERVATIONS`. The uploader collects JSON observation files from the internal observations folder, decrypts/unzips each, verifies checksums, queries CB for upload/DRP status, batches up to 10 files into a 7z archive, and uploads via `/observations` API. Manages disk quota (250MB cap, 10000 file limit). Driver images are included in the zip if available.

**When active:** Always
**Frequency:** Periodic (observation_frequency config, default ~10 minutes)
**Cross-service impact:** circular_buffer (upload status check, DRP), health service (obs gen metrics)

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| `TC_unifieduploader_468` | Upload frequency matches config |
| `TC_unifieduploader_723` | Pending obs handled at bootup |
| `TC_unifieduploader_472` | File ownership check |
| `TC_unifieduploader_473` | Observation encryption |
| `TC_unifieduploader_474` | Zero-size file check |
| `TC_unifieduploader_475` | Obs count vs outage minutes |
| `TC_unifieduploader_477` | Obs count < 10000 limit |
| `TC_unifieduploader_518` | Obs folder size < 250MB |
| `TC_unifieduploader_664` | Batch size ≤ 10 |
| `TC_unifieduploader_667` | Obs upload retry |
| `TC_unifieduploader_668` | File decryption in unoperated obs |
| `TC_unifieduploader_677` | Back-to-back obs uploads |
| `TC_unifieduploader_679` | Priority 2 for obs |
| `TC_unifieduploader_754` | Summary JSON in SD card |

---

### Flow 7: Log Upload (Critical / Non-Critical / Syslog)

**What happens:** Other services trigger log upload via `REQ_UPLOAD_CRITICAL_LOGS`, `REQ_UPLOAD_CRITICAL_LOGS_TIME_RANGE`, or `REQ_UPLOAD_NON_CRITICAL_LOGS`. The uploader collects log files from archive folders by time range, zips them, and uploads to cloud. When syslog is enabled (`[log] enable_syslog = true`), syslog .gz files are included from the archived syslog path.

**When active:** Always (critical), config-gated (non-critical, syslog)
**Frequency:** On demand (triggered by other services, typically keep_alive_manager)
**Cross-service impact:** keep_alive_manager (triggers log upload), cloud (receives logs)

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| `TC_unifieduploader_117` | Critical-only log upload when non-critical disabled |
| `TC_unifieduploader_1544` | Zip archive of critical logs |
| `TC_unifieduploader_789` | Log upload frequency |
| `TC_unifieduploader_801` | Default config API payloads |
| `TC_unifieduploader_802` | Back-to-back log uploads |
| `TC_unifieduploader_828` | Zip archive validation |

---

### Flow 8: DRP (Data Retention Policy)

**What happens:** When `[drp] enabled = 1`, the uploader blocks upload of files older than `clock_hours` (default 72 hours). It subscribes to CB broadcast of the oldest uploadable file timestamp and uses a 60-second TimerTick to periodically clean up old EA images. VOD requests for files older than the DRP threshold are failed with reason "DRP".

**When active:** Only when `[drp] enabled = 1`
**Frequency:** CB broadcast check every 60s, VOD DRP check per request
**Cross-service impact:** circular_buffer (provides oldest uploadable file timestamp via NDMB broadcast)

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| `TC_unifieduploader_2086` | VOD blocked by DRP |
| `TC_unifieduploader_2753` | DRP + VOD interaction |
| `TC_unifieduploader_2754` | Tier transition with DRP |

---

### Flow 9: Privacy Mode (Record/Upload Privacy)

**What happens:** When privacy is enabled, VOD uploads are blocked with failure reason "Record privacy is enabled" or "Upload privacy is enabled". The uploader checks privacy status before attempting any video upload.

**When active:** When privacy mode is enabled in device config
**Frequency:** Per VOD request
**Cross-service impact:** None (config-driven check)

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| `TC_unifieduploader_2051` | VOD blocked in privacy mode |
| `TC_unifieduploader_2081` | Upload privacy blocks VOD |

---

### Flow 10: LLA (Low Latency Alert) Upload

**What happens:** When LLA is enabled (`[low_latency_alert_notification] enabled = true` in nd_config.ini), a dedicated thread subscribes to the LLA messenger socket, receives alert JSON messages, parses UUID and event_code, queues by priority (high-G > mod-G > low-G), and uploads via `/lla` API with short timeouts (5s connect, 10s max). Retries up to `lla_retry_limit` (default 3) with 5s sleep between retries.

**When active:** Only when `[low_latency_alert_notification] enabled = true` in nd_config.ini AND messenger socket paths configured in nd_core_common.ini
**Frequency:** On event (each LLA alert)
**Cross-service impact:** inference/ndcentral (generates LLA messages), health service (LLA metrics)

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| (LLA test cases are currently not present — LLA is typically disabled on most device configs as seen in device logs: "LLA not enabled. Exiting thread..!") | | |

---

### Flow 11: EA (Extended Analytics) Images Upload

**What happens:** When EA is enabled (`[ea_config] enabled = 1`), the uploader syncs the EA images folder and DB at bootup, monitors folder size, and processes `REQ_UPLOAD_EA_IMAGES` requests. EA images are batched (up to 10), zipped, and uploaded via the observations API. The EA DB tracks image metadata with a 24000-entry limit.

**When active:** Only when `[ea_config] enabled = 1`
**Frequency:** On demand (triggered by ndcentral/inference)
**Cross-service impact:** inference (generates EA images), circular_buffer (DRP status for cleanup)

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| (EA images test cases reference EA behavior indirectly through observations and bootup flows) | | |

---

### Flow 12: Sign Crops Upload

**What happens:** When `REQ_UPLOAD_SIGN_CROPS` is received, the uploader collects sign crop image files, creates a 7z archive, and uploads with metadata (jobId, status, drp, timestamp). Failed uploads are retried.

**When active:** Always (when sign crop requests arrive)
**Frequency:** On demand
**Cross-service impact:** inference (generates sign crops)

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| (Sign crop test cases are currently tested as part of broader upload validation flows) | | |

---

### Flow 13: VOD Elapsed Time Tracking

**What happens:** A dedicated thread updates the `ELAPSED_TIME_MIN` column in the VOD DB table every 3 minutes. This tracks how long each VOD request has been pending. VODs exceeding `vod_timeout_hrs` (default 72 hours) are timed out.

**When active:** Always
**Frequency:** Every 3 minutes
**Cross-service impact:** None (internal bookkeeping)

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| (Elapsed time tracking is validated indirectly via VOD timeout and pending VOD test cases) | | |

---

### Flow 14: Pending Upload Status (IGN OFF)

**What happens:** When ignition turns OFF, the uploader reports pending VOD count, D-HUB offline status, consecutive failure count, and internet status to power_monitor via message queue. This helps power_monitor decide whether to keep the device awake for pending uploads.

**When active:** Always (triggered by IGN OFF)
**Frequency:** On IGN OFF, then every 60s while IGN is OFF
**Cross-service impact:** power_monitor (receives pending status)

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| `TC_unifieduploader_726` | Handles LPM entry |
| `TC_unifieduploader_755` | Survives IGN OFF |

---

### Flow 15: Upload Retry & Resilience

**What happens:** Failed uploads are retried with configurable retry limits (default max 10 for events, 6 for VODs). The uploader persists all requests in SQLite DB so they survive reboots. On boot, failed uploads are re-queued. Exponential/linear backoff is used for repeated failures. Consecutive VOD upload failures are tracked for health reporting.

**When active:** Always
**Frequency:** Per failed upload
**Cross-service impact:** None (internal retry logic)

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| `TC_unifieduploader_122` | Upload retry mechanism |
| `TC_unifieduploader_742` | Survives conn_mgr disable |
| `TC_unifieduploader_743` | Survives cyclic reboot |
| `TC_unifieduploader_757` | Survives abrupt power off |

---

### Flow 16: DB & Storage Management

**What happens:** The uploader uses two SQLite databases: the main uploader DB (tracks events, VODs, upload status) and the EA DB (tracks EA images). It also manages SD card mount status, validates file paths, and handles SD card failures gracefully.

**When active:** Always
**Frequency:** Per upload request
**Cross-service impact:** None (internal storage)

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| `TC_unifieduploader_467` | DB state transitions |
| `TC_unifieduploader_696` | SD card status check |
| `TC_unifieduploader_703` | Mount path validation |
| `TC_unifieduploader_704` | DB fields validation |
| `TC_unifieduploader_705` | VOD DB fields |
| `TC_unifieduploader_732` | Uploader table entries |
| `TC_unifieduploader_740` | VOD table entries |
| `TC_unifieduploader_741` | SD unmount handling |

---

### Flow 17: Health Stats Reporting

**What happens:** The uploader reports upload metrics (obs gen, upload add/del, alert info, signal info, VOD health) to the health service via `send_msg_healthstats`. A dedicated thread polls conn_mgr for signal info and attaches it to alert upload sessions.

**When active:** Always
**Frequency:** Per upload event + periodic signal info collection
**Cross-service impact:** health service (receives metrics), conn_mgr (signal info)

**Test cases that validate this flow:**
| Test Case ID | What it checks |
|---|---|
| `TC_unifieduploader_659` | HS request loop halt |

---

## Config-Driven Flow Activation

The agent MUST read the device config from `device_data/device_<ID>_config.ini`
before selecting test cases. Use the following mapping:

| Config Section | Config Key | Value | Activates Flow(s) | Test Cases Affected |
|---|---|---|---|
| — | — | — | Startup & Init (always active) | TC_464, TC_465, TC_466, TC_827, TC_556, TC_832, TC_3490, TC_719, TC_720 |
| — | — | — | Event/Alert Upload (always active) | TC_601, TC_670, TC_844, TC_597, TC_2049 |
| — | — | — | VOD Upload (always active) | TC_86, TC_2050, TC_2052, TC_2082, TC_2496, TC_124, TC_126, TC_2068, TC_2099, TC_2116, TC_2117, TC_2118, TC_2122, TC_2197, TC_2198, TC_2551, TC_2555, TC_2545, TC_845, TC_2070, TC_3486, TC_3488, TC_2580, TC_2561, TC_2578, TC_2579, TC_2495 |
| — | — | — | Observations Upload (always active) | TC_468, TC_723, TC_472, TC_473, TC_474, TC_475, TC_477, TC_518, TC_664, TC_667, TC_668, TC_677, TC_679, TC_754 |
| — | — | — | Log Upload (always active) | TC_117, TC_1544, TC_789, TC_801, TC_802, TC_828 |
| `[drp]` | `enabled` | `1` | DRP enforcement | TC_2086, TC_2753, TC_2754 |
| `[log]` | `enable_syslog` | `true` | Syslog upload path change | (affects TC_117, TC_828 behavior) |
| `[log]` | `enable_non_critical` | `true` | Non-critical log uploads | TC_117 |
| `[low_latency_alert_notification]` | `enabled` | `true` (in nd_config.ini) | LLA upload thread | (no dedicated TCs currently) |
| `[ea_config]` | `enabled` | `1` | EA images upload | (affects bootup sync and EA upload flows) |
| `[uploader_settings]` | `vod_timeout_hrs` | `<N>` (default 72) | VOD timeout threshold | TC_124 |
| `[uploader_settings]` | `conn_timeout_alert` | `60-120` (default 90) | CURL connect timeout for alerts | TC_832 |
| `[uploader_settings]` | `max_timeout_alert` | `100-200` (default 120) | CURL max timeout for alerts | TC_832 |
| `[uploader_settings]` | `lla_retry_limit` | `1-20` (default 3) | LLA retry limit | (LLA flow) |
| `[uploader_settings]` | `curl_init_once_flag` | `1` | CURL init once at startup | TC_3490 |
| `[drp]` | `clock_hours` | `72/120/168/240/720` | DRP retention window | TC_2086, TC_2753 |
| `[upload_settings]` | `observation_frequency` | `<N>` (minutes) | Obs upload interval | TC_468 |
| privacy config | record/upload privacy | enabled | Blocks VOD uploads | TC_2051, TC_2081 |
| `[hdmaps_mode]` | `enable` | `true` | HDMaps mode flag in obs upload | (affects obs upload payload) |

**Rules:**
- Flows marked "always active" → run their test cases unconditionally
- Flows gated by a config key → run only if the device config has that key set to the activating value
- If the config key is missing from the device config → use the default value listed above
- Config values in `device_list_config.csv` take precedence if present (they reflect live production config)

---

## Cross-Service Dependencies

| Related Service | Why | When to check its logs |
|---|---|
| `awsiot` | Receives VOD requests from cloud, forwards to uploader | When validating VOD flow (TC_86 steps 4-6) |
| `circular_buffer` (`circ_buff`) | Provides file availability, DRP status, stores alert copies | When validating VOD CB interaction, observations CB check, DRP |
| `power_monitor` (`power_mon`) | Sends IGN on/off events, receives pending upload status | When validating IGN-related flows (TC_726, TC_755) |
| `health` | Receives upload metrics, signal info, obs gen info, VOD health | When validating health stats reporting (TC_597, TC_659) |
| `conn_mgr` | Provides signal info (RSSI, RSRP, etc.) for alert uploads | When validating signal info collection (TC_597) |
| `keep_alive_manager` | Triggers critical/non-critical log upload requests | When validating log upload flows (TC_117, TC_789) |
| `ext_cam` | Fetches external camera video files for VOD | When validating external VOD flows |
| `ndcentral` | Triggers observations upload, generates alerts | When validating obs and alert flows |
| `inference` | Generates EA images, sign crops, LLA messages | When validating EA/sign crop/LLA flows |

---

## Flow Dependency Graph

```
boot
 ├─ init_server_params() → parse configs → read DRP/syslog/LLA/EA flags
 ├─ init CURL
 ├─ create DBs (uploader_db, ea_db)
 ├─ create message queues
 ├─ subscribe to CB broadcast (OLDEST_UPLOADABLE_FILE)
 ├─ cleanup stale files (trim_vod_out, ea_zips)
 ├─ restore failed uploads from DB → re-queue
 ├─ create threads:
 │   ├─ PS (handle_req_upload) ──────────── events, observations
 │   ├─ PL (handle_req_upload_long) ─────── VODs
 │   ├─ TH_LOG_UPL (handle_req_upload_misc) logs, sign crops, EA, VOD list
 │   ├─ PLLA (handle_req_upload_lla) ────── LLA (if enabled)
 │   ├─ TH_VOD_ELAPSED_TIME ────────────── VOD elapsed time + DRP tick
 │   ├─ TH_UPLOADER_DATA_UPLOAD_PENDING ── pending status on IGN OFF
 │   └─ HS thread ──────────────────────── signal info collection
 └─ main loop (message receive):
     ├─ POWERMON_IGNITION → update igni_status → notify pending thread
     ├─ REQ_UPLOAD_EVENT → copy to CB → insert DB → push to PS queue
     ├─ REQ_UPLOAD_OBSERVATIONS → push to PS queue (deduplicated)
     ├─ REQ_UPLOAD_EA_IMAGES → push to TH_LOG_UPL queue (deduplicated)
     ├─ REQ_UPLOAD_CRITICAL_LOGS → push to TH_LOG_UPL queue
     ├─ REQ_UPLOAD_NON_CRITICAL_LOGS → push to TH_LOG_UPL queue
     ├─ REQ_UPLOAD_SIGN_CROPS → push to TH_LOG_UPL queue
     ├─ REQ_UPLOAD_VOD_LIST → push to TH_LOG_UPL queue
     ├─ REQ_UPLOAD_VOD → persist in DB → push to PL queue (or ext_vod fetch)
     └─ RES_FETCH_EXT_VOD → update ext VOD status → push to PL queue
```

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Determine active flows** using the Config-Driven Flow Activation table above
3. **For each active flow**, look up the mapped test cases for `unifieduploader`
4. **From each test case**, use `acceptance_criteria` for log patterns and device-type paths from device config
5. **Search device logs** in `device_logs/<device_id>/unifieduploader.log` using patterns from the test case
6. **For cross-service checks**, also search logs of related services listed in Cross-Service Dependencies
7. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED
8. **Note:** Many flows (observations, log uploads) occur multiple times per drive session — validate ALL occurrences using the 95% threshold rule
9. **Note:** The log format is: `<epoch_ms>: <uptime_ms>: <TAG>: <LEVEL>: <PID>: <TID>: <message>` — TAG values include `UPL`, `DRP`, `UDB`, `UPL_HS`, `vod_health`, `PLLA`, `SYS_U`, `FILE_U`, `CFG_PRSR`, `PROP_U`, `DB_U`, `NDMBC`, `TTICK`, `SUTILS`
