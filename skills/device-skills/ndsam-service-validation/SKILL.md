---
name: ndsam-service-validation
description: "Use when: validating nd_sam service behavior from device logs. Covers service init and config parsing, MSGQ creation (MQ_SAM), SAM DB creation (sam_gen.db / sam_cfd.db), log file creation, password remaining-time countdown (~60s), counter sync to IoT (SYNC_COUNTER_WITH_IOT), password change flow (secret key + registration), sam_gen.db deletion reset, pass_rotate/pass_interval config, reboot-near-expiry password reset, KA command execution (auto-pwd, manual, no-network, back-to-back, LPW), log upload via KAM, DB retention after disable, and audit log generation."
argument-hint: "device ID or nd_sam test tag (e.g., /ndsam-service-validation 103452403525)"
---

# nd_sam — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the `nd_sam` service —
> what it does, how its flows relate to each other, and which config keys activate which flows.
> The agent reads pytest test cases in `tests/ndsam/` for exact log patterns and acceptance
> criteria — this skill does NOT duplicate those.

---

## Service Overview

`nd_sam` (Netradyne Secure Access Manager) is a long-running C++ daemon that manages rotating
SSH/system passwords for the device. It generates a per-device password, stores it in a SQLite
database (`sam_gen.db`), counts elapsed time since the last password change, and rotates the
password when `ELAPSED_TIME >= pass_interval_h * 3600`. It syncs its password counter with
AWS IoT on every start and keeps-alive that sync on a 60-second retry loop. It also services
KA (keep-alive) commands from the cloud that embed the current SAM password.

**Process name:** `nd_sam`
**Log file:** `/home/ubuntu/.nddevice/log/nd_sam/` (bagheera/octo)
**Log format:** `<epoch_ms>: <uptime_ms>: <TAG>: <level>: <pid>: <tid>: <message>`
**Config file:** `/home/ubuntu/config/sam_config.ini` (or via bagheera_override.ini section `[sam]`)
**SAM DB path by device type:**

| Device Type          | DB Path                                 |
| -------------------- | --------------------------------------- |
| bagheera / bagheera2 | `/home/ubuntu/.nddevice/sam_db/`        |
| bagheera3 / octo     | `/home/ubuntu/.nddevice/sam_db/`        |
| krait / krait2       | `/data/nd_files/db/sam_db/`             |

**MSGQ name:** `MQ_SAM`
**MSGQ file path:** `/dev/shm/MSGQ/MQ_SAM`

> **Note on krait**: Most test cases skip krait/krait2 — check `test_precondition_skip_krait`
> in each test. DB paths differ (see table above) and some features are bagheera-only.

---

## Log Format

```
<epoch_ms>: <uptime_ms>: <TAG>: <level>: <pid>: <tid>: <message>
```

Key tags:
- `ND_SAM:` — main service logic (config, MSGQ, password, counter, KA command)
- `CFG_PRSR:` — config file parsing at startup
- `SHDW:` — shadow password utility (user existence, password change validation)
- `PERS_H:` — persistence helper (DB read/write operations)
- `PERS_DB:` — SQLite DB layer (open, exec, close)
- `MSGQ:` / `MSGQ_U:` — message queue creation/client
- `SUTILS:` — service utilities (NDService object, signal registration)
- `TTICK:` — timer tick thread (retry timeout, password countdown)
- `MAIN:` — main thread init status

---

## Service Flows

### Flow 1: Service Initialization & Config Parsing

**What happens:** At startup, `nd_sam` registers signals, creates the `NDService` object, then
parses `bagheera_override.ini` followed by the SAM config. It reads and logs five config keys:
`device_id`, `interval_hours`, `pass_rotate`, `event_retry_interval`, and `enable_service`.
After config read, it verifies the OS user exists and can have its password changed (SHDW
check), creates the `MQ_SAM` message queue server, opens `sam_gen.db`, reads persisted generic
data, and enters the main message loop.

**When active:** Every service start / reboot
**Frequency:** Once per start

**Key log patterns:**
```
ND_SAM: ... Entered Init
SUTILS: ... Creating NDService object: nd_sam
SUTILS: ... Signals registered
ND_SAM: ... Entered ReadConfigData
ND_SAM: ... device_id: 103452403525
ND_SAM: ... interval_hours: 24
ND_SAM: ... pass_rotate is true
ND_SAM: ... event_retry_interval: 60
ND_SAM: ... enable_service is true
ND_SAM: ... ReadConfigData success
SHDW: ... Inside DoesUserExistWithPassword
SHDW: ... Valid user, password can be changed
MSGQ: ... Message queue server created MQ_SAM <ipc_key> <token> 35
ND_SAM: ... SAM_Server_MQ created successfully
PERS_H: ... directory already exists
ND_SAM: ... Init success
MAIN: ... Init success
ND_SAM: ... Entered Run
PERS_H: ... Inside function: ReadSamPwGenericData
PERS_H: ... File:/home/ubuntu/.nddevice/sam_db/sam_gen.db exists
PERS_DB: ... SQL DB Successfully opened for: /home/ubuntu/.nddevice/sam_db/sam_gen.db with auto commit status: 1
PERS_DB: ... sqlite3_exec success: /home/ubuntu/.nddevice/sam_db/sam_gen.db, 0
PERS_H: ... Data read success in ReadSamPwGenericData
PERS_DB: ... Closed DB successfully: /home/ubuntu/.nddevice/sam_db/sam_gen.db, 0
ND_SAM: ... AuthModule::ReadPersistedGenericData success
ND_SAM: ... On start, sync counter
ND_SAM: ... issue file opened
ND_SAM: ... Entered MsgLoop
```

**Normal startup errors (non-fatal, expected):**
```
MSGQ: E: ... Error creating token SM 20, Error msg: No such file or directory
MSGQ: E: ... mq is not valid
MSGQ_U: E: ... Cannot create message queue client: SM
```
> SM (service manager) is not yet running at nd_sam startup — this error is expected and harmless.

```
ND_SAM: E: ... invalid audit_log_level from sam_conf: 0
```
> Audit log is disabled by default (level=0) — this E-level log is normal/expected.

```
PERS_DB: E: ... DB already NULL: /home/ubuntu/.nddevice/sam_db/sam_gen.db
```
> Happens after close when double-close is attempted — non-fatal, expected after every DB operation.

**Default config values (TC_1123):**

| Key                    | Default Value |
| ---------------------- | ------------- |
| `version`              | `0.0.1`       |
| `uname`                | `ubuntu` / `root` |
| `pass_interval_h`      | `24`          |
| `pass_rotate`          | `1` (true)    |
| `event_retry_interval_s` | `60`        |
| `audit_log_enabled`    | `0` (disabled) |

**Test cases that validate this flow:**

| Test Case ID | pytest Path                                                               | What it checks                                              |
| ------------ | ------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `TC_1092`    | `tests/ndsam/test_tc_ndsam_1092_default_config_service_status_check.py`   | `enabled=1` in sam_config.ini + `nd_sam` service is active  |
| `TC_1123`    | `tests/ndsam/test_tc_ndsam_1123_default_config_values_check.py`           | All 5 default config key values                             |
| `TC_1101`    | `tests/ndsam/test_tc_ndsam_1101_disable_from_config_check.py`             | Set `enabled=0` → reboot → nd_sam is not running            |

---

### Flow 2: MSGQ Creation (MQ_SAM)

**What happens:** On every start (reboot, normal start, after crash/diff reboot, after cyclic
reboot, after LPW), `nd_sam` creates a message queue server named `MQ_SAM`. The file appears
at `/dev/shm/MSGQ/MQ_SAM` with permissions `srw-rw-rw-` (`srwxrwxrwx` after masking).

**MSGQ file permissions check:**
```bash
ls -l /dev/shm/MSGQ/MQ_SAM | awk '{print $1}' | cut -d '.' -f 1
# Expected: srwxrwxrwx  (or srw-rw-rw- depending on umask)
```

**When active:** Every service start
**Frequency:** Once per start

**Key log patterns:**
```
MSGQ: ... Message queue server created MQ_SAM <ipc_key> <token> 35
ND_SAM: ... SAM_Server_MQ created successfully
```

**Bagheera diff / power_mon diff reboot trigger:**
```
# In SVC logs (ndcentral / bagheera watchdog):
bagheera diff timeout
# In power_mon logs:
power_monitor diff timeout
```
After such a reboot, `nd_sam` will start normally and create `MQ_SAM` again.

**Test cases that validate this flow:**

| Test Case ID | pytest Path                                                               | What it checks                                              |
| ------------ | ------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `TC_1093`    | `tests/ndsam/test_tc_ndsam_1093_msgq_creation_permissions_check.py`       | MSGQ creation log + `/dev/shm/MSGQ/MQ_SAM` permissions     |
| `TC_1208`    | `tests/ndsam/test_tc_ndsam_1208_msgq_creation_post_reboot_check.py`       | MSGQ created after normal reboot                            |
| `TC_1206`    | `tests/ndsam/test_tc_ndsam_1206_msgq_creation_post_ndcentral_diff_check.py` | MSGQ created after bagheera diff timeout reboot           |
| `TC_1207`    | `tests/ndsam/test_tc_ndsam_1207_msgq_creation_post_powermon_diff_check.py` | MSGQ created after power_monitor diff timeout reboot      |
| `TC_1241`    | `tests/ndsam/test_tc_ndsam_1241_msgq_creation_post_cyclic_reboot_check.py` | MSGQ created after cyclic reboot (`CYCLIC:REBOOT` in power_mon) |
| `TC_1209`    | `tests/ndsam/test_tc_ndsam_1209_msgq_creation_in_lpw_check.py`            | MSGQ created during LPW wakeup cycle                        |
| `TC_1224`    | `tests/ndsam/test_tc_ndsam_1224_msgq_creation_post_camcrash_reboot_check.py` | MSGQ created after camera crash reboot                   |

---

### Flow 3: SAM DB Creation & Schema

**What happens:** On every boot, `nd_sam` creates (or opens) two SQLite databases in
`sam_db/`: `sam_gen.db` (generic password data — counter, elapsed time, password hash) and
`sam_cfd.db` (confidential/config data). Both must exist post-reboot. When SAM is
**disabled**, the DBs are NOT created (even if they previously existed and were deleted).
When SAM is **re-enabled**, both DBs are created on next boot.

**sam_gen.db schema (COUNTER_DATA table):**
```
sqlite3 /home/ubuntu/.nddevice/sam_db/sam_gen.db ".schema"
# Expected table: COUNTER_DATA with columns including ELAPSED_TIME, PASS_IN_EFFECT_TIME, etc.
```

**DB creation verification:**
```bash
# bagheera:
ls /home/ubuntu/.nddevice/sam_db/sam_gen.db
ls /home/ubuntu/.nddevice/sam_db/sam_cfd.db
# krait:
ls /data/nd_files/db/sam_db/sam_gen.db
ls /data/nd_files/db/sam_db/sam_cfd.db
```

**When active:** Every boot (when SAM enabled)
**Frequency:** Once per boot

**Key log patterns:**
```
PERS_H: ... File:/home/ubuntu/.nddevice/sam_db/sam_gen.db exists
PERS_DB: ... SQL DB Successfully opened for: /home/ubuntu/.nddevice/sam_db/sam_gen.db with auto commit status: 1
PERS_DB: ... sqlite3_exec success: /home/ubuntu/.nddevice/sam_db/sam_gen.db, 0
PERS_H: ... Data read success in ReadSamPwGenericData
PERS_DB: ... Closed DB successfully: /home/ubuntu/.nddevice/sam_db/sam_gen.db, 0
```

**Test cases that validate this flow:**

| Test Case ID | pytest Path                                                               | What it checks                                              |
| ------------ | ------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `TC_1094`    | `tests/ndsam/test_tc_ndsam_1094_sam_db_creation_post_reboot_check.py`     | `sam_cfd.db` and `sam_gen.db` exist post reboot             |
| `TC_1096`    | `tests/ndsam/test_tc_ndsam_1096_samgen_db_fields_check.py`                | `sam_gen.db` has correct schema/fields                      |
| `TC_1257`    | `tests/ndsam/test_tc_ndsam_1257_db_creation_only_post_config_enable_check.py` | DBs absent when SAM disabled; created on re-enable      |
| `TC_1259`    | `tests/ndsam/test_tc_ndsam_1259_db_retention_post_disabling_sam.py`       | DBs retained (not deleted) when SAM disabled                |

---

### Flow 4: Log File Creation & Epoch Format

**What happens:** After reboot, `nd_sam` creates a new log file in its log directory. The
log file's modification timestamp must be >= the service's `ActiveEnterTimestamp`. All
timestamps in the log must be in epoch millisecond format (13–14 digit integers), not human-
readable date strings.

**Log dir path:**
- bagheera/octo: `/home/ubuntu/.nddevice/log/nd_sam/`
- krait: `/data/nd_files/.nddevice/log/nd_sam/`

**Epoch format validation (TC_1129):**
```bash
# Every line's first field must be a 13-14 digit epoch:
awk -F: '{print $1}' /home/ubuntu/.nddevice/log/nd_sam/<logfile> | grep -v '^[0-9]\{13,14\}$'
# Should return nothing (0 non-epoch lines)
```

**When active:** Every boot
**Frequency:** Once per boot

**Test cases that validate this flow:**

| Test Case ID | pytest Path                                                               | What it checks                                              |
| ------------ | ------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `TC_1097`    | `tests/ndsam/test_tc_ndsam_1097_logfile_creation_post_bootup_check.py`    | Log file exists and was created after service start time    |
| `TC_1129`    | `tests/ndsam/test_tc_ndsam_1129_log_timestamp_epoch_format_check.py`      | All log timestamps are 13–14 digit epoch format             |

---

### Flow 5: Password Remaining Time Countdown (every 60s)

**What happens:** After startup, `nd_sam` spawns a `PasswordTimerTickCB` thread (via
`StartPassTimeoutThread`) that fires every **60 seconds**. Each tick:
1. Opens `sam_gen.db` and calls `UpdateDbWithPassInEffectTime` to update `PASS_IN_EFFECT_TIME`
2. Logs `Password remaining time: <N> as on: <epoch_s>`

The remaining time decrements by 60 each tick (e.g., 51060 → 51000 → 50940 → ...).
When remaining time reaches 0 (i.e., `ELAPSED_TIME >= pass_interval_h * 3600`), the password
is rotated.

**Relationship:** `elapsed_time + remaining_time == pass_interval_h * 3600`
- Default: `pass_interval_h = 24` → `86400 seconds` total
- At any tick: `remaining_time = 86400 - ELAPSED_TIME`

**Key log patterns (every 60s):**
```
ND_SAM: ... PasswordTimerTickCB called
PERS_H: ... Inside function: UpdateDbWithPassInEffectTime
PERS_H: ... File:/home/ubuntu/.nddevice/sam_db/sam_gen.db exists
PERS_DB: ... SQL DB Successfully opened for: /home/ubuntu/.nddevice/sam_db/sam_gen.db with auto commit status: 1
PERS_DB: ... sqlite3_exec success: /home/ubuntu/.nddevice/sam_db/sam_gen.db, 0
PERS_DB: ... Closed DB successfully: /home/ubuntu/.nddevice/sam_db/sam_gen.db, 0
ND_SAM: ... Password remaining time: 51060 as on: 1779371843
ND_SAM: ... PasswordTimerTickCB called
...
ND_SAM: ... Password remaining time: 51000 as on: 1779371903
```

**Query current elapsed time directly:**
```bash
sqlite3 /home/ubuntu/.nddevice/sam_db/sam_gen.db "SELECT ELAPSED_TIME FROM COUNTER_DATA;"
```

**LPW / poweroff: elapsed time does NOT count while device is powered off** (TC_1246).
When ignition is off and device is in LPW, the `PasswordTimerTickCB` is not running.

**Test cases that validate this flow:**

| Test Case ID | pytest Path                                                               | What it checks                                              |
| ------------ | ------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `TC_1122`    | `tests/ndsam/test_tc_ndsam_1122_password_et_rt_check.py`                  | `elapsed_time + remaining_time == pass_interval_h * 3600`   |
| `TC_1143`    | `tests/ndsam/test_tc_ndsam_1143_pass_interval_config_in_hours_check.py`   | Remaining time is in seconds (converted from hours config)  |
| `TC_1246`    | `tests/ndsam/test_tc_ndsam_1246_pwd_remaining_time_during_poweroff_calculation_check.py` | ET does not advance while powered off |

---

### Flow 6: Counter Sync to IoT (SYNC_COUNTER_WITH_IOT)

**What happens:** On every start, `nd_sam` reads the current counter from `sam_gen.db` and
attempts to send a `SYNC_COUNTER_WITH_IOT` message to the `AWSIOT` message queue. At startup
`AWSIOT` may not yet be running, causing an initial failure. A retry event (event ID `2`) is
registered with a 60-second `TTICK` timer. After ~60s, the retry fires and sends successfully
if `AWSIOT` is available. On success, `OnIotCounterSyncResponse` receives
`counter sync response: 1`.

**Retry mechanism:**
- Event 2 = SYNC_COUNTER_WITH_IOT (IoT counter sync)
- Event 3 = ReportDataToHealthStats (HS health payload)
- Both are retried on the same 60s timer tick

**Key log patterns (startup — initial failure):**
```
ND_SAM: ... On start, sync counter
MSGQ: E: ... Error creating token AWSIOT 14, Error msg: No such file or directory
MSGQ: E: ... mq is not valid
MSGQ_U: E: ... Cannot create message queue client: AWSIOT
ND_SAM: E: ... SYNC_COUNTER_WITH_IOT failed to be sent, counter: 60
ND_SAM: ... Entered RetryEvent, event to retry: 2
ND_SAM: ... Entered SetEventForRetry, event to retry: 2
ND_SAM: ... events_to_retry size: 1
ND_SAM: ... Entered StartRetryEventTimeoutThread
TTICK: ... Inside RegisterCB
TTICK: ... Interval: 60
TTICK: ... Inside Start
TTICK: ... TimerTick::Run thread created
ND_SAM: ... event_notifier_tick_ started successfully
```

**Key log patterns (~60s later — retry success):**
```
TTICK: ... Inside Run
ND_SAM: ... Retry Event cb triggered at: <epoch_s>
ND_SAM: ... Received RETRY_EVENT_TIMEOUT msg from: MQ_SAM
ND_SAM: ... Entered OnRetryEventTimeout
ND_SAM: ... AuthModule::ReadPersistedGenericData success
ND_SAM: ... SYNC_COUNTER_WITH_IOT message sent successfully, counter: 60
ND_SAM: ... Data reported successfully to HealthStats
ND_SAM: ... Entered RetryEventClear
ND_SAM: ... Entered ResetEventForRetry, event to clear: 3
ND_SAM: ... Event: 3 cleared
```

**Key log patterns (AWSIOT response):**
```
ND_SAM: ... Received SYNC_COUNTER_RESPONSE msg from: AWSIOT
ND_SAM: ... Entered OnIotCounterSyncResponse, counter sync response: 1
ND_SAM: ... Entered RetryEventClear
ND_SAM: ... Entered ResetEventForRetry, event to clear: 2
ND_SAM: ... Event: 2 cleared
ND_SAM: ... Entered StopRetryEventTimeoutThread
ND_SAM: ... event_notifier_tick stop response: 1
ND_SAM: ... StopRetryEventTimeoutThread status: 1
```

**Counter value persists across reboots** (TC_1142). It only resets when `sam_gen.db` is
deleted (TC_1124). Manual password change via `nd_sam_cli` increments the counter by 1
(TC_1141).

**Test cases that validate this flow:**

| Test Case ID | pytest Path                                                               | What it checks                                              |
| ------------ | ------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `TC_1141`    | `tests/ndsam/test_tc_ndsam_1141_manually_change_password_counter_increment_check.py` | Counter increments after manual password change |
| `TC_1142`    | `tests/ndsam/test_tc_ndsam_1142_counter_value_post_reboot_check.py`       | Counter unchanged across multiple reboots                   |
| `TC_1136`    | `tests/ndsam/test_tc_ndsam_1136_pwd_and_counter_post_disabling_sam_check.py` | Counter preserved when SAM disabled                     |

---

### Flow 7: Password Change Flow (Secret Key + Registration)

**What happens:** When `ELAPSED_TIME >= pass_interval_h * 3600` (or triggered manually via
`nd_sam_cli`), `nd_sam`:
1. Calls the secret key registration API
2. Receives registration success
3. Generates a new password
4. Changes the OS password via SHDW shadow utility
5. Logs `Password changed successfully`
6. Resets `ELAPSED_TIME` to 0 in `sam_gen.db`
7. Increments the counter by 1
8. Sends `SYNC_COUNTER_WITH_IOT` with new counter

**Cloud login / `nd_sam_cli` trigger (TC_1128):** When a user logs in via `nd_sam_cli`
(cloud password change), elapsed time is reset to 0 and tracking restarts.

**When `pass_rotate = 0` (TC_1210):** Password is NOT rotated — device uses the default
static password. SAM service still runs but does not change the password.

**Key log patterns:**
```
ND_SAM: ... Password changed successfully
SHDW: ... Inside DoesUserExistWithPassword
SHDW: ... Valid user, password can be changed
```

**After password change, verify in DB:**
```bash
sqlite3 /home/ubuntu/.nddevice/sam_db/sam_gen.db "SELECT ELAPSED_TIME FROM COUNTER_DATA;"
# Should be 0 (reset after change)
```

**Test cases that validate this flow:**

| Test Case ID | pytest Path                                                               | What it checks                                              |
| ------------ | ------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `TC_1135`    | `tests/ndsam/test_tc_ndsam_1135_password_change_flow_check.py`            | Secret key API call + registration success + new password   |
| `TC_1128`    | `tests/ndsam/test_tc_ndsam_1128_cloud_pwd_login_et_track_check.py`        | Cloud login via nd_sam_cli resets ELAPSED_TIME to 0         |
| `TC_1210`    | `tests/ndsam/test_tc_ndsam_1210_default_pwd_login_post_enabling_sam_with_config_change_check.py` | `pass_rotate=0` → default password used |

---

### Flow 8: Delete sam_gen.db → Password + Counter Reset

**What happens:** When `sam_gen.db` is deleted and the service is restarted or device is
rebooted, `nd_sam` creates a fresh DB, generates a new password, and resets the counter to 0.
The new password will differ from the pre-deletion password.

**Steps:**
```bash
# bagheera:
rm /home/ubuntu/.nddevice/sam_db/sam_gen.db
# Then: systemctl restart nd_sam   OR   device reboot
```

**Expected after recreation:**
- New `sam_gen.db` created
- Counter = 0 (reset)
- New password ≠ old password

**Test cases that validate this flow:**

| Test Case ID | pytest Path                                                               | What it checks                                              |
| ------------ | ------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `TC_1124`    | `tests/ndsam/test_tc_ndsam_1124_deleting_sam_gen_db_check.py`             | New password generated + counter reset after DB deletion    |

---

### Flow 9: Pass Rotate Config — Disabled, Corrupt, Interval Change

**Three sub-cases:**

**9a. Invalid `pass_rotate` value (TC_1137):** If `pass_rotate` is set to an invalid value
(e.g., `5`), `nd_sam` must NOT trigger back-to-back reboots. The service reads the value, logs
it, and ignores it (or treats as disabled). The device boots normally.

**9b. `pass_interval_h` change (TC_1250):** When `pass_interval_h` is changed (e.g., from 24
to 48), `nd_sam` uses the new interval. Remaining time reflects the new interval in seconds
(`48 * 3600 = 172800s`). Test sets `ELAPSED_TIME = 172680` (2 min before 48h expiry) and
verifies password rotates on reboot.

```bash
sqlite3 /home/ubuntu/.nddevice/sam_db/sam_gen.db "UPDATE COUNTER_DATA SET ELAPSED_TIME=172680"
```

**9c. Service disabled check (TC_1101):** After setting `sam.enabled=0` in override config
and rebooting, `nd_sam` must not be active:
```bash
systemctl is-active nd_sam
# Expected: inactive or failed
```

**Test cases that validate this flow:**

| Test Case ID | pytest Path                                                               | What it checks                                              |
| ------------ | ------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `TC_1137`    | `tests/ndsam/test_tc_ndsam_1137_pass_rotate_config_corrupt_check.py`      | Invalid `pass_rotate=5` → no back-to-back reboots           |
| `TC_1250`    | `tests/ndsam/test_tc_ndsam_1250_pass_interval_config_modification_check.py` | `pass_interval_h=48` applied; password rotates at 48h    |
| `TC_1101`    | `tests/ndsam/test_tc_ndsam_1101_disable_from_config_check.py`             | `enabled=0` → nd_sam service not running                    |

---

### Flow 10: Reboot Near Expiry → Password Reset

**What happens:** If the device reboots (or crank-shuts-down or camera crashes) when the
elapsed time is within ~1 minute of the password change threshold, `nd_sam` detects on next
boot that the password is due and performs the rotation. This validates the "remaining time
carried across reboot" behavior using the persisted `ELAPSED_TIME` in `sam_gen.db`.

**Test setup pattern — set ET near expiry:**
```bash
# For 24h interval (86400s), reboot 1 min prior = 86400 - 60 = 86340s
sqlite3 /home/ubuntu/.nddevice/sam_db/sam_gen.db "UPDATE COUNTER_DATA SET ELAPSED_TIME=86160"
# Or for cyclic reboot test (6 min prior, allows time for cyclic reboot):
sqlite3 /home/ubuntu/.nddevice/sam_db/sam_gen.db "UPDATE COUNTER_DATA SET ELAPSED_TIME=85800"
```

**Reboot trigger types:**
- Normal reboot (TC_1221)
- Crank shutdown (TC_1222): `shutdown_reason = CRANK:SHUTDOWN` in power_mon logs
- Camera crash reboot (TC_1223): camera service crash triggers watchdog reboot
- Cyclic reboot (TC_1242): `CYCLIC:REBOOT` in power_mon logs after `cyclic_reboot_duration` minutes

**Expected after reboot:**
- `Password changed successfully` in nd_sam logs post-reboot
- New password ≠ old password (from before ET was set)

**Test cases that validate this flow:**

| Test Case ID | pytest Path                                                               | What it checks                                              |
| ------------ | ------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `TC_1221`    | `tests/ndsam/test_tc_ndsam_1221_device_pwd_reset_after_rebooting_1min_prior.py` | Password reset after normal reboot 1 min before expiry |
| `TC_1222`    | `tests/ndsam/test_tc_ndsam_1222_crankshutdown_1min_prior_pwd_reset_check.py` | Password reset after crank shutdown near expiry         |
| `TC_1223`    | `tests/ndsam/test_tc_ndsam_1223_cam_crash_reboot_1min_prior_pwd_reset_check.py` | Password reset after cam crash reboot near expiry    |
| `TC_1242`    | `tests/ndsam/test_tc_ndsam_1242_device_pwd_reset_after_cyclic_reboot_1min_prior.py` | Password reset after cyclic reboot near expiry    |

---

### Flow 11: KA Command Execution

**What happens:** The cloud sends Keep-Alive (KA) commands to the device that include a SAM
password for authentication. `nd_sam` provides the current password for auto-population.
KA commands are executed by the `keep_alive_manager` (KAM) service; `nd_sam` provides the
password via its MQ.

**Four sub-cases:**

**11a. Auto-populated password (TC_1160):** Cloud sends KA command with auto-populate flag.
`nd_sam` injects the current password. KAM executes with that password.

**11b. Manual password (TC_1161):** Cloud sends KA command with a hardcoded password
(e.g., `EKM2800123Netra`). `nd_sam` passes it through as-is. KAM executes with the manual
password.

**11c. No-network execution (TC_1178):** After pushing KA command, block API network access.
KAM attempts execution, fails to reach the API endpoint, and logs the failure. No
`EXECUTED_SUCCESS` entry appears.

**11d. Back-to-back execution (TC_1179):** Push 3 KA commands sequentially (e.g.,
`systemctl status bagheera`, `systemctl status nd_sam`, `systemctl status wifi_mgr`). All
3 execute in order. KAM logs `EXECUTED_SUCCESS` for each.

**11e. LPW (Low Power Wakeup) KA (TC_1162):** Turn off ignition → device enters LPW → push
KA command while in LPW. `crank_level 0` must appear in power_mon logs. KA executes on next
wakeup.

**Test cases that validate this flow:**

| Test Case ID | pytest Path                                                               | What it checks                                              |
| ------------ | ------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `TC_1160`    | `tests/ndsam/test_tc_ndsam_1160_ka_command_autopopulate_pwd_check.py`     | Auto-populated SAM password used in KA command execution    |
| `TC_1161`    | `tests/ndsam/test_tc_ndsam_1161_ka_command_manual_pwd_check.py`           | Manual password passed through in KA command                |
| `TC_1178`    | `tests/ndsam/test_tc_ndsam_1178_ka_command_no_network_execution_check.py` | KA call fails without network                               |
| `TC_1179`    | `tests/ndsam/test_tc_ndsam_1179_ka_command_back_to_back_execution_check.py` | 3 back-to-back KA commands all execute                    |
| `TC_1162`    | `tests/ndsam/test_tc_ndsam_1162_ka_command_lpw_check.py`                  | KA command pushed and executed during LPW cycle             |

---

### Flow 12: Log Upload & Deletion via KAM

**What happens:** The `keep_alive_manager` (KAM) service periodically compresses and uploads
`nd_sam` logs to the cloud, then deletes them locally. The validation checks KAM logs (not
nd_sam logs) for entries showing that nd_sam log files were picked, zipped, and then removed.

**Where to look:** KAM log directory: `/home/ubuntu/.nddevice/log/keep_alive_manager/` (or
similar).

**Test cases that validate this flow:**

| Test Case ID | pytest Path                                                               | What it checks                                              |
| ------------ | ------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `TC_1144`    | `tests/ndsam/test_tc_ndsam_1144_log_upload_and_deletion_check.py`         | KAM logs show nd_sam logs compressed + deleted after upload |

---

### Flow 13: DB Retention After SAM Disabled

**What happens:** When SAM is disabled via config (`enabled=0`) and the device is rebooted,
the `sam_gen.db` and `sam_cfd.db` databases are **retained** (not deleted). The service simply
does not start — but existing DBs are preserved for when SAM is re-enabled.

This is the inverse of TC_1257 (which tests DB creation on re-enable): here the focus is that
disabling does NOT delete existing DBs.

**Test cases that validate this flow:**

| Test Case ID | pytest Path                                                               | What it checks                                              |
| ------------ | ------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `TC_1259`    | `tests/ndsam/test_tc_ndsam_1259_db_retention_post_disabling_sam.py`       | Both DBs still present after disabling SAM + reboot         |

---

### Flow 14: Audit Log Generation & Disable

**What happens:** When `audit_log_enabled = 1` in `sam_config.ini`, `nd_sam` generates audit
log entries in addition to normal logs. When disabled (default: `audit_log_enabled = 0`),
the startup log shows: `invalid audit_log_level from sam_conf: 0` (which is the expected
non-fatal E-level log indicating audit is off).

**Enable audit logging (test setup):**
```ini
# In sam_config.ini or bagheera_override.ini:
audit_log_enabled = 1
```

**Expected behavior when enabled:** Audit-specific log patterns appear in nd_sam logs.
**Expected behavior when disabled:** No new audit patterns after 90s wait (TC_1272 verifies
count does not increase after disabling).

**Test cases that validate this flow:**

| Test Case ID | pytest Path                                                               | What it checks                                              |
| ------------ | ------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `TC_1272`    | `tests/ndsam/test_tc_ndsam_1272_audit_log_generation_check.py`            | Audit logs appear when enabled; stop appearing when disabled |

---

## Config-Driven Flow Activation

| Config Section | Config Key               | Value       | Activates Flow(s)                              | Test Cases                            |
| -------------- | ------------------------ | ----------- | ---------------------------------------------- | ------------------------------------- |
| `[sam]`        | `enabled`                | `1` / `true` | Flows 1–14 (all flows, service runs)           | TC_1092, all                          |
| `[sam]`        | `enabled`                | `0` / `false` | Flow 13: DB retained, service not running     | TC_1101, TC_1257, TC_1259             |
| `[sam]`        | `pass_rotate`            | `1`         | Flow 7: Automatic password rotation            | TC_1135, TC_1221, TC_1222, TC_1223, TC_1242 |
| `[sam]`        | `pass_rotate`            | `0`         | Password NOT rotated (static default password) | TC_1210                               |
| `[sam]`        | `pass_rotate`            | invalid (e.g. `5`) | No back-to-back reboots (graceful ignore) | TC_1137                            |
| `[sam]`        | `pass_interval_h`        | `24` (default) | 86400s password rotation interval          | TC_1122, TC_1143                      |
| `[sam]`        | `pass_interval_h`        | `48`        | 172800s rotation interval                      | TC_1250                               |
| `[sam]`        | `event_retry_interval_s` | `60`        | Flow 6: 60s SYNC_COUNTER retry timer           | TC_1141, TC_1142                      |
| `[sam]`        | `audit_log_enabled`      | `0` (default) | Normal startup; audit E-log is expected       | TC_1272                               |
| `[sam]`        | `audit_log_enabled`      | `1`         | Flow 14: Audit log entries generated           | TC_1272                               |

**Default values** (when key absent from config):
- `enabled` → `1` (service runs)
- `pass_rotate` → `1` (rotation active)
- `pass_interval_h` → `24`
- `event_retry_interval_s` → `60`
- `audit_log_enabled` → `0` (disabled)

---

## Cross-Service Dependencies

| Related Service        | Why                                                                          | When to check its logs              |
| ---------------------- | ---------------------------------------------------------------------------- | ----------------------------------- |
| `AWSIOT` / `aws_iot`   | Receives `SYNC_COUNTER_WITH_IOT` msg; returns `SYNC_COUNTER_RESPONSE`        | Flow 6 (TC_1141, TC_1142)           |
| `HealthStatsManager`   | Receives `ReportDataToHealthStats` health payload; retried if not ready       | Flow 6 (startup retry)              |
| `keep_alive_manager`   | Executes KA commands using nd_sam password; uploads/deletes nd_sam logs       | Flow 11 (TC_1160–1179), Flow 12 (TC_1144) |
| `bagheera` (ndcentral) | Diff timeout triggers reboot → nd_sam recreates MSGQ on next start           | Flow 2 (TC_1206)                    |
| `power_monitor`        | Diff timeout triggers reboot; LPW state; crank shutdown reason                | Flow 2 (TC_1207), Flow 10 (TC_1222), Flow 11 (TC_1162) |
| `conn_mgr`             | LTE connectivity required for counter sync to reach AWS IoT                   | Flow 6 (when sync fails)            |
| `SM` (service manager) | `SM 20` MSGQ error at startup is normal — SM not yet running when nd_sam starts | Flow 1 (startup error)             |

---

## Flow Dependency Graph

```
boot → [Flow 1: Config Parsing + Init] → [Flow 2: MQ_SAM MSGQ creation]
     → [Flow 3: sam_gen.db + sam_cfd.db open/create]
     → [Flow 4: Log file created post-boot]
     → [Flow 5: PasswordTimerTickCB every 60s] → decrement remaining_time → DB update
     → [Flow 6: SYNC_COUNTER_WITH_IOT on start]
         → AWSIOT not ready → retry at 60s → SYNC_COUNTER_RESPONSE
         → HealthStats not ready → retry at 60s → Data reported
     → [Flow 7: Password change] — when ET >= interval OR manual trigger
         → ELAPSED_TIME reset to 0 → counter++ → SYNC_COUNTER_WITH_IOT sent

sam_gen.db deleted → [Flow 8: Password + Counter Reset]
pass_rotate / pass_interval config → [Flow 9: Config variants]
reboot near ET expiry → [Flow 10: Password Reset After Reboot]
    (normal reboot / crank shutdown / cam crash / cyclic reboot)
cloud command → [Flow 11: KA Command] → keep_alive_manager executes with SAM password
KAM log retention → [Flow 12: Log Upload + Deletion]
SAM disabled → [Flow 13: DB Retained, Service Off]
audit_log_enabled=1 → [Flow 14: Audit Log Generation]
```

---

## Validation Instructions for the Agent

1. **Device type** determines DB path — use `DB_PATH_MAP` from the test file or the table above
2. **krait/krait2 skip**: most test cases call `test_precondition_skip_krait` — check device type before running
3. **Normal E-level logs to ignore** (do NOT flag as failures):
   - `MSGQ: E: Error creating token SM 20` — SM not running at nd_sam start
   - `ND_SAM: E: invalid audit_log_level from sam_conf: 0` — audit disabled, expected
   - `PERS_DB: E: DB already NULL` — normal double-close pattern after every DB operation
   - `MSGQ: E: Error creating token AWSIOT 14` / `Error creating token HS 11` — early startup, retried at 60s
4. **For Flow 6 (counter sync)**: verify initial failure (`SYNC_COUNTER_WITH_IOT failed`) at t=0,
   then success at t=~60s (`SYNC_COUNTER_WITH_IOT message sent successfully`), then
   `Received SYNC_COUNTER_RESPONSE msg from: AWSIOT`
5. **For Flow 5 (remaining time)**: `remaining_time` should decrement by 60 between consecutive
   `PasswordTimerTickCB` entries; verify using `grep "Password remaining time" <log_file> | tail -3`
6. **For Flow 10 (reboot near expiry)**: confirm `ELAPSED_TIME` was set in DB before reboot,
   then confirm `Password changed successfully` appears in logs AFTER the reboot timestamp
7. **For Flow 3 (DB creation)**: check both `sam_gen.db` and `sam_cfd.db` — both must exist
8. **For Flow 2 (MSGQ)**: check `ls /dev/shm/MSGQ/MQ_SAM` exists AND log shows
   `Message queue server created MQ_SAM`
9. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED / SKIPPED
