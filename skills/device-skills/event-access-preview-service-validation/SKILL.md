---
name: event-access-preview-service-validation
description: "Use when: validating Event Access Preview (EAP) service behavior from device logs. Covers config parsing & initialization, storage layout (sdcard ea/ folder + ea.db schema), image capture & session flow, image capture frequency, crank-off & low-power-wakeup behavior, privacy mode interactions (full off-duty, full outward, partial outward, partial off-duty), DB removal & corruption recovery, service stop/restart/crash resilience, cyclic reboot survival, uploader DB sync & EA zip upload, and health payload EA field validation."
argument-hint: "device ID (e.g., /event-access-preview-service-validation 440073)"
---

# Event Access Preview (`ndcentral` / `unifieduploader`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the Event Access Preview
> feature — what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads Python test cases for actual log patterns, device-type paths,
> and acceptance criteria — this skill does NOT duplicate those.

---

## Service Overview

Event Access Preview (EAP) is a feature within the `ndcentral` service that periodically captures a JPEG snapshot (thumbnail) from the outward and/or inward camera once per recording session. Snapshots are named after the session file (`<session>.0_ea.jpeg`), copied to the sdcard `ea/` folder, and registered in `ea.db` (SQLite, table `UPLOADER_EA_IMGS`). The `unifieduploader` service picks up entries from `ea.db`, batches them into a ZIP (`ea_zips/`), and uploads to the Netradyne cloud.

EAP is gated by `[ea_config] enabled = 1` in `bagheera_override.ini`. Under full privacy (off-duty full / outward full) images are captured but immediately deleted and never stored. Under partial privacy modes images are stored (blurred). The `awsiot` health payload reports `ea_enabled` when the feature is active.

**Process names:** `ndcentral` (capture), `unifieduploader` (upload)
**Log files:** `ndcentral/log_*.log`, `unifieduploader.log`
**Primary config section:** `[ea_config]`

---

## Device-Type Paths

| Device Type     | ea.db path                        | sdcard path                        | ea_zips path                                  | EA capture log pattern                                   |
| --------------- | --------------------------------- | ---------------------------------- | --------------------------------------------- | -------------------------------------------------------- |
| `krait`/`krait2`| `/data/nd_files/db/ea.db`         | `/data/nd_files/nd_sdcard`         | `/data/nd_files/state_files/ea_zips`          | `Event Access session filename: /home/iriscli/files/0<ea_file>` |
| All others      | `/home/ubuntu/.nddevice/ea.db`    | `/media/data/nd_sdcard`            | `/home/ubuntu/.nddevice/ea_zips`              | `Filename EA: /home/iriscli/files/0<ea_file>`            |

---

## Service Flows

### Flow 1: Config Parsing & Initialization

**What happens:** At startup ndcentral reads `[ea_config]` from `bagheera_config.ini` (merged from `bagheera_override.ini`). It logs whether the feature is enabled or disabled, the resolved image parameters (`ea_image_params`), the capture rate (`ea_images_per_hr`), and the batch frequency. All subsequent EAP flows depend on `enabled = 1` being parsed here.

**When active:** Always at every service start — determines if EAP runs at all
**Frequency:** Once per boot / service restart
**Cross-service impact:** Config read here controls image capture in Flow 3 and DB sync in Flow 9

**Test cases that validate this flow:**
| Test Case ID    | Python Path                                                                               | What it checks                                                    |
| --------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `TC_eap_2054`   | `tests/event_access_preview/test_TC_eap_2054_config_parsing_flow.py`                     | All `[ea_config]` keys parsed and written to `bagheera_config.ini` |
| `TC_eap_2201`   | `tests/event_access_preview/test_TC_eap_2201_health_payload_keys.py`                     | `ea_image_params: width 206, height 112, quality 70` in ndcentral log; `ea_enabled` key in awsiot health payload |

---

### Flow 2: Storage Layout

**What happens:** On first run (or after EA is enabled), ndcentral creates the `ea/` subdirectory under the device's sdcard path. The `unifieduploader` creates and owns `ea.db` with a single table `UPLOADER_EA_IMGS` (columns: `INDEXID`, `NAME`, `STATUS`, `SESSION`). The db file is owned `root:root` with `rw-r--r--` permissions.

**When active:** Always when EAP is enabled
**Frequency:** Folder and DB created once; persist across reboots
**Cross-service impact:** ndcentral writes images to `ea/`; unifieduploader reads/writes `ea.db`

**Test cases that validate this flow:**
| Test Case ID    | Python Path                                                                               | What it checks                                                    |
| --------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `TC_eap_2055`   | `tests/event_access_preview/test_TC_eap_2055_folder_to_store_event_access.py`            | `{sdcard}/ea/` folder exists on the correct sdcard path           |
| `TC_eap_2066`   | `tests/event_access_preview/test_TC_eap_2066_check_db_presence_fields.py`                | `ea.db` present, correct permissions, `UPLOADER_EA_IMGS` table with all 4 columns |

---

### Flow 3: Image Capture & Session Flow

**What happens:** Once per recording session, ndcentral captures a JPEG thumbnail and names it `<session_name>.0_ea.jpeg`. The file is written to `/home/iriscli/files/` first, then copied to `{sdcard}/ea/`. An entry is then inserted into `ea.db` (`UPLOADER_EA_IMGS`) with the filename and status. The full path to the captured image is logged. File naming differs by device type (see Device-Type Paths table above).

**When active:** When `[ea_config] enabled = 1` and `outward = 1` (or `inward = 1`)
**Frequency:** Once per session (~every 60 seconds while recording)
**Cross-service impact:** `ea.db` entry triggers `unifieduploader` to batch into ZIP (Flow 9)

**Test cases that validate this flow:**
| Test Case ID              | Python Path                                                                               | What it checks                                                    |
| ------------------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `TC_eap_2073`             | `tests/event_access_preview/test_TC_eap_2073_session_flow.py`                            | Full flow: EA enabled → image captured → on sdcard → in ea.db → uploader zip created |
| `TC_eap_2090`             | `tests/event_access_preview/test_TC_eap_2090_2091_partial_alert_session.py`              | EA image captured and stored in DB during a partial session       |
| `TC_eap_2091`             | `tests/event_access_preview/test_TC_eap_2090_2091_partial_alert_session.py`              | EA image captured and stored in DB during an alert session        |
| `TC_eap_2119`/`2126`/`2128`/`2129` | `tests/event_access_preview/test_TC_eap_2119_time_difference_between_images.py` | Image capture interval matches `num_images_per_hour`: 60→60s, 30→120s, 20→180s, 12→300s |

---

### Flow 4: Crank-Off & Low-Power-Wakeup Behavior

**What happens:** On crank-off (relay off), ndcentral flushes the current session and stops image capture. After `crank_shutdown_duration` elapses the device enters low-power mode. During a Low-Power Wakeup (LPW) window, ndcentral restarts, logs `Event Access Preview Feature is enabled`, and resumes image capture. After crank-on (relay on), a new session begins and EA resumes normally.

**When active:** When `[power] crank_shutdown_duration` is configured; LPW flow additionally requires `lowpower_wakeup_duration` and `lowpower_wakeup_cycle_duration`
**Frequency:** Event-driven (relay toggle)
**Cross-service impact:** `power_monitor` tracks LPW wakeup count; ndcentral re-initializes EAP on each wakeup

**Test cases that validate this flow:**
| Test Case ID              | Python Path                                                                               | What it checks                                                    |
| ------------------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `TC_eap_2056`             | `tests/event_access_preview/test_TC_eap_2056_flow_during_crank_off.py`                   | EA enabled before crank off; new session + EA resume after crank on |
| `TC_eap_2092`             | `tests/event_access_preview/test_TC_eap_2092_2108_2113_low_power_mode.py`                | EA active and image captured during LPW wakeup window             |
| `TC_eap_2108`             | `tests/event_access_preview/test_TC_eap_2092_2108_2113_low_power_mode.py`                | EA behavior is correct in low power mode transitions              |
| `TC_eap_2113`             | `tests/event_access_preview/test_TC_eap_2092_2108_2113_low_power_mode.py`                | LPW cycle timing validates `POWER_MONITOR_ctx->lowpower_wakeups 1` |

---

### Flow 5: Privacy Mode Interactions

**What happens:** EAP behavior depends on the active privacy mode:
- **Full off-duty privacy** (`off_duty_mode = true`, long-press activated): EA captures the image but logs `Deleting ea image file` and does NOT copy to sdcard or add to DB.
- **Full outward privacy** (`[privacy_mode] outward = true`, ignition-based): Same — image deleted immediately, `Deleting ea image file` logged.
- **Partial outward privacy** (`outward_partial_privacy = true`): Image IS stored (blurred/modified); sdcard `ea/` count > 0.
- **Partial off-duty privacy** (`off_duty_partial_privacy = true`): Image IS stored; sdcard `ea/` count > 0.

**When active:** Determined by privacy config; mode changes are event-driven (ignition, long-press)
**Frequency:** Per image capture attempt
**Cross-service impact:** Privacy state read from ndcentral; EA images never reach `ea.db` under full privacy

**Test cases that validate this flow:**
| Test Case ID    | Python Path                                                                               | What it checks                                                    |
| --------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `TC_eap_2135`   | `tests/event_access_preview/test_TC_eap_2135_offduty_privacy.py`                         | Full off-duty privacy → `Deleting ea image file` logged, sdcard `ea/` count = 0 |
| `TC_eap_2141`   | `tests/event_access_preview/test_TC_eap_2141_outward_privacy.py`                         | Full outward privacy → `Deleting ea image file` logged, sdcard `ea/` count = 0 |
| `TC_eap_2733`   | `tests/event_access_preview/test_TC_eap_2733_outward_partial_privacy.py`                 | Outward partial privacy → images stored on sdcard (count > 0)     |
| `TC_eap_2744`   | `tests/event_access_preview/test_TC_eap_2744_offduty_partial_privacy.py`                 | Off-duty partial privacy → images stored on sdcard (count > 0)    |

---

### Flow 6: DB Removal & Corruption Recovery

**What happens:** If `ea.db` is deleted or corrupted (garbage data written), `unifieduploader` detects the invalid state at next startup or DB access. It recreates the table, logs recovery, and resumes normal operation. Image capture by ndcentral is unaffected — new images continue to appear on sdcard. After recreation, new DB entries are inserted for subsequent captures.

**When active:** Triggered when `ea.db` is missing or its schema is invalid
**Frequency:** Self-healing; recovery happens at next service start or DB access
**Cross-service impact:** ndcentral continues capturing to sdcard even without a valid DB

**Test cases that validate this flow:**
| Test Case ID    | Python Path                                                                               | What it checks                                                    |
| --------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `TC_eap_2132`   | `tests/event_access_preview/test_TC_eap_2132_remove_ea_db.py`                            | DB removed → ndcentral still captures images → DB recreated → entries present |
| `TC_eap_2153`   | `tests/event_access_preview/test_TC_eap_2153_db_corrupted.py`                            | DB corrupted with garbage → bagheera restart → DB valid again → EA images still captured |

---

### Flow 7: Service Stop / Restart / Crash Resilience

**What happens:** EAP is resilient to individual service failures:
- **ndcentral stop**: watchdog restarts it; EA logs `Event Access Preview Feature is enabled` on re-init.
- **Bagheera service restart**: EA re-initializes; `Event Access Preview Feature is enabled` appears post-restart.
- **Uploader stop**: Image capture continues; images queue on sdcard. On uploader restart, `EA Images folder and DB sync started` is logged.
- **Camera process crash**: bagheera auto-recovers; EA resumes.
- **Uploader crash (SIGKILL)**: uploader auto-restarts via systemd; `ea.db` remains intact.

**When active:** Always; resilience tested by injecting stop/kill during active EAP session
**Frequency:** Event-driven
**Cross-service impact:** Upload backlog builds up on sdcard when uploader is down; synced on recovery

**Test cases that validate this flow:**
| Test Case ID              | Python Path                                                                               | What it checks                                                    |
| ------------------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `TC_eap_2144`             | `tests/event_access_preview/test_TC_eap_2144_2145_2170_2171_service_stop.py`             | ndcentral stop → `Event Access Preview Feature is enabled` after restart |
| `TC_eap_2145`             | `tests/event_access_preview/test_TC_eap_2144_2145_2170_2171_service_stop.py`             | Uploader stop → EA images still captured on sdcard                |
| `TC_eap_2170`             | `tests/event_access_preview/test_TC_eap_2144_2145_2170_2171_service_stop.py`             | Bagheera service restart → EA resumes after restart               |
| `TC_eap_2171`             | `tests/event_access_preview/test_TC_eap_2144_2145_2170_2171_service_stop.py`             | Uploader restart → `EA Images folder and DB sync started` logged  |
| `TC_eap_2162`             | `tests/event_access_preview/test_TC_eap_2162_2191_camera_uploader_crash.py`              | Camera crash → bagheera recovers → EA resumes                     |
| `TC_eap_2191`             | `tests/event_access_preview/test_TC_eap_2162_2191_camera_uploader_crash.py`              | Uploader SIGKILL crash → uploader restarts → `ea.db` still accessible |

---

### Flow 8: Cyclic Reboot Survival

**What happens:** After a device reboot (cyclic or service reboot), ndcentral re-initializes EAP and logs `Event Access Preview Feature is enabled`. The `unifieduploader` triggers `EA Images folder and DB sync started` to reconcile the sdcard `ea/` folder with `ea.db` (recovering any images that were captured but not yet DB-registered before the reboot). Existing DB entries are preserved.

**When active:** Always after any device reboot
**Frequency:** Once per boot
**Cross-service impact:** DB sync by unifieduploader ensures no images are lost across reboots

**Test cases that validate this flow:**
| Test Case ID    | Python Path                                                                               | What it checks                                                    |
| --------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `TC_eap_2177`   | `tests/event_access_preview/test_TC_eap_2177_2180_cyclic_reboot.py`                      | EA resumes + `EA Images folder and DB sync started` after cyclic reboot |
| `TC_eap_2180`   | `tests/event_access_preview/test_TC_eap_2177_2180_cyclic_reboot.py`                      | EA resumes after a second consecutive reboot                       |

---

### Flow 9: Uploader EA Upload & DB Sync

**What happens:** The `unifieduploader` (`TH_LOG_UPL` thread) monitors `ea.db` for unuploaded entries. It batches JPEG files into a ZIP archive in `ea_zips/` and uploads to cloud. On startup (or restart), it runs a folder-DB sync (`EA Images folder and DB sync started`) to detect files on sdcard that are missing from DB or files in DB that no longer exist on sdcard. When EAP is **disabled**, no sync is triggered. Concurrent API calls (e.g., push_alert) do not interrupt EA upload.

**When active:** When EAP is enabled; sync runs on every unifieduploader startup
**Frequency:** Upload: per `ea_batch_frequency` interval; sync: once per uploader start
**Cross-service impact:** awsiot health payload includes `ea_enabled` field

**Test cases that validate this flow:**
| Test Case ID    | Python Path                                                                               | What it checks                                                    |
| --------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `TC_eap_2185`   | `tests/event_access_preview/test_TC_eap_2185_image_deleted_from_sdcard.py`               | Image deleted from sdcard → uploader restart → `EA Images folder and DB sync started` |
| `TC_eap_2186`   | `tests/event_access_preview/test_TC_eap_2186_2194_uploader_api_calls.py`                 | Push_alert during EA → EA capture uninterrupted, ea.db count > 0  |
| `TC_eap_2194`   | `tests/event_access_preview/test_TC_eap_2186_2194_uploader_api_calls.py`                 | Push_alert API call coexists with EA upload in progress           |
| `TC_eap_2196`   | `tests/event_access_preview/test_TC_eap_2196_db_sync_feature_disabled.py`                | EAP disabled → no DB sync; EAP enabled → sync triggered           |

---

### Flow 10: DRP Interaction

**What happens:** When Data Retention Policy (DRP) is disabled, DRP-related log lines are still printed by ndcentral. This validates the DRP code path executes regardless of DRP state and does not suppress EA-related log output.

**When active:** Always (DRP config present or absent)
**Frequency:** Continuous log output
**Cross-service impact:** None — this is a logging-level validation

**Test cases that validate this flow:**
| Test Case ID    | Python Path                                                                               | What it checks                                                    |
| --------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `TC_eap_2727`   | `tests/event_access_preview/test_TC_eap_2727_drp_logs_disabled.py`                       | DRP log lines present in ndcentral even when DRP is disabled; DRP config key exists in `bagheera_config.ini` |

---

## Config-Driven Flow Activation

The agent MUST read the device config from `device_data/device_<ID>_config.ini` (or the device's `/home/ubuntu/.nddevice/bagheera_config.ini`) before selecting test cases.

| Config Section       | Config Key                  | Value    | Activates Flow(s)                                      | Test Cases Affected                                           |
| -------------------- | --------------------------- | -------- | ------------------------------------------------------ | ------------------------------------------------------------- |
| `[ea_config]`        | `enabled`                   | `1`      | All EAP flows (1–10)                                   | All TC_eap_* test cases                                       |
| `[ea_config]`        | `enabled`                   | `0`      | Disabled state validation (Flow 9 — no sync)           | `TC_eap_2054`, `TC_eap_2196`                                  |
| `[ea_config]`        | `outward`                   | `1`      | Flow 3: Outward camera capture                         | All session flow TCs                                          |
| `[ea_config]`        | `inward`                    | `1`      | Flow 3: Inward camera capture                          | Session flow TCs (if inward camera present)                   |
| `[ea_config]`        | `num_images_per_hour`       | `60`     | Flow 3: 1 image/min interval                           | `TC_eap_2119`                                                 |
| `[ea_config]`        | `num_images_per_hour`       | `30`     | Flow 3: 2 min interval                                 | `TC_eap_2126`                                                 |
| `[ea_config]`        | `num_images_per_hour`       | `20`     | Flow 3: 3 min interval                                 | `TC_eap_2128`                                                 |
| `[ea_config]`        | `num_images_per_hour`       | `12`     | Flow 3: 5 min interval                                 | `TC_eap_2129`                                                 |
| `[ea_config]`        | `ea_batch_frequency`        | N        | Flow 9: Upload batch interval (minutes)                | `TC_eap_2073`, `TC_eap_2196`                                  |
| `[privacy_mode]`     | `off_duty_mode`             | `true`   | Flow 5: Full off-duty privacy → images deleted         | `TC_eap_2135`                                                 |
| `[privacy_mode]`     | `off_duty_partial_privacy`  | `true`   | Flow 5: Partial off-duty → images stored               | `TC_eap_2744`                                                 |
| `[privacy_mode]`     | `outward`                   | `true`   | Flow 5: Full outward privacy → images deleted          | `TC_eap_2141`                                                 |
| `[camera]`           | `outward_partial_privacy`   | `true`   | Flow 5: Partial outward → images stored                | `TC_eap_2733`                                                 |
| `[power]`            | `crank_shutdown_duration`   | N        | Flow 4: Crank-off behavior                             | `TC_eap_2056`, `TC_eap_2092`, `TC_eap_2108`, `TC_eap_2113`   |
| `[power]`            | `lowpower_wakeup_duration`  | N        | Flow 4: LPW wakeup behavior                            | `TC_eap_2092`, `TC_eap_2108`, `TC_eap_2113`                   |
| —                    | —                           | —        | Flows 6–8, 10 (always active when EA enabled)          | Recovery, resilience, reboot, and DRP TCs                     |

**Rules:**
- Flows marked "always active when EA enabled" → run unconditionally if `[ea_config] enabled = 1`
- Privacy flows → run only if the matching privacy config key is present
- If `[ea_config]` section is absent from device config → treat as `enabled = 0` (disabled)
- Config values in `device_list_config.csv` take precedence if present

---

## Cross-Service Dependencies

| Related Service      | Why                                                                          | When to check its logs                            |
| -------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------- |
| `ndcentral`          | Captures EA JPEG per session; logs `Filename EA:` / `Event Access session filename:` | All flows — primary capture source            |
| `unifieduploader`    | Batches images into ZIP; manages `ea.db`; logs `EA Images folder and DB sync started` | Flows 9 (upload/sync), 7 (uploader stop/crash), 8 (reboot sync) |
| `awsiot`             | Health payload reports `ea_enabled` key                                      | Flow 1 / TC_eap_2201 — health payload validation  |
| `power_monitor`      | Tracks LPW wakeup count (`lowpower_wakeups`)                                 | Flow 4 — LPW behavior (TC_eap_2092/2108/2113)     |
| `bagheera`           | Service wrapper for ndcentral; restart resilience tested                     | Flow 7 — service restart TCs                      |

---

## Flow Dependency Graph

```
boot → [Flow 1: Config Parse] → ea_enabled?
    YES → [Flow 2: Storage Layout] → ea/ folder + ea.db ready
         → [Flow 3: Image Capture] → per-session JPEG → sdcard ea/ → ea.db entry
             → [Flow 9: Uploader DB Sync] → ZIP batch upload to cloud
         → [Flow 5: Privacy Check] → full privacy? → delete image
                                   → partial privacy? → store image (blurred)
         → [Flow 4: Crank/LPW Events] → crank off → flush → LPW wakeup → resume
    NO  → [Flow 9 (disabled)] → no DB sync triggered

failure events:
  → [Flow 6: DB Recovery]     → DB removed/corrupt → recreated on service restart
  → [Flow 7: Resilience]      → service stop/crash → auto-restart → EA resumes
  → [Flow 8: Reboot Survival] → reboot → EA reinit + uploader DB sync
  → [Flow 10: DRP]            → DRP logs printed regardless of DRP state
```

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini` (or `/home/ubuntu/.nddevice/bagheera_config.ini` on device)
2. **Determine device type** (`krait`, `krait2`, `bagheera2`, `bagheera3`, `bagheera4`, `octo`) — gates DB path, sdcard path, ea_zips path, and log pattern
3. **Check `[ea_config] enabled`** — if `0` or absent, only run `TC_eap_2054` and `TC_eap_2196` (disabled-state checks)
4. **For each active flow**, run the mapped Python test cases from `tests/event_access_preview/`
5. **Search device logs** in `device_logs/<device_id>/` — primary logs are `ndcentral.log` and `unifieduploader.log`
6. **For privacy flows**, also check ndcentral for `Deleting ea image file` vs sdcard `ea/` file count
7. **For upload/sync flows**, check unifieduploader for `EA Images folder and DB sync started`
8. **For health payload**, check awsiot log for `ea_enabled`
9. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED (if the flow's config gate was not active)

### Key Log Patterns

| Pattern (ndcentral)                                      | Confirms                                      |
| -------------------------------------------------------- | --------------------------------------------- |
| `Event Access Preview Feature is enabled`                | EAP active (Flows 1, 3–8)                     |
| `Event Access Preview Feature is disabled`               | EAP inactive (TC_eap_2196 negative check)      |
| `ea_image_params: width 206, height 112, quality 70`     | Config parsed correctly (Flow 1)               |
| `ea_config: ea_images_per_hr 60`                         | Capture frequency set (Flow 1)                 |
| `Filename EA: /home/iriscli/files/0<ea_file>`            | Image captured — non-krait/krait2 (Flow 3)     |
| `Event Access session filename: /home/iriscli/files/0<ea_file>` | Image captured — krait/krait2 (Flow 3)  |
| `Deleting ea image file`                                 | Full privacy active → image discarded (Flow 5) |
| `Privacy Mode is Activated`                              | Outward privacy triggered (TC_eap_2141/2733)   |
| `Off duty mode is Activated`                             | Off-duty privacy triggered (TC_eap_2135/2744)  |

| Pattern (unifieduploader)                                | Confirms                                      |
| -------------------------------------------------------- | --------------------------------------------- |
| `EA Images folder and DB sync started`                   | DB sync running (Flows 8, 9)                   |

### DB Queries for Validation
```bash
# Verify table exists
sqlite3 <db_path> ".tables"

# Check schema
sqlite3 <db_path> "PRAGMA table_info(UPLOADER_EA_IMGS);"

# Count entries
sqlite3 <db_path> "SELECT COUNT(*) FROM UPLOADER_EA_IMGS;"

# Find specific image
sqlite3 <db_path> "SELECT * FROM UPLOADER_EA_IMGS WHERE NAME LIKE '%<ea_file>%';"
```
