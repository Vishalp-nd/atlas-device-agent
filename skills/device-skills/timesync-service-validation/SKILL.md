---
name: timesync-service-validation
description: "Use when: validating time_sync service behavior from device logs. Covers service init and config parsing, message queue creation, GPS time sync (~15s after boot), LTE/network time sync (~60s via AT+CCLK), UDID increment after reboot, GPS check interval (≥30s), drive-time update, NTP disabled check, log presence, RTC jump recording, low_power_wakeup_count (lpw) propagation, bootup timing comparison, delayed service start, future/past time correction, and disabled-service behavior."
argument-hint: "device ID (e.g., /timesync-service-validation 103452403525)"
---

# time_sync — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the `time_sync` service —
> what it does, how its flows relate to each other, and which config keys activate which flows.
> The agent reads pytest test cases in `tests/timesync/` for exact log patterns and acceptance
> criteria — this skill does NOT duplicate those.

---

## Service Overview

`time_sync` is a long-running C++ daemon that synchronizes the device system clock using two
sources: **GPS** (primary, ~15s after boot once GPS fix is obtained) and **LTE/network**
(secondary, via `AT+CCLK` modem command, every ~60s). On each boot it creates a new UDID
(Unique Drive ID = `prev_udid + 1`), persists it in a SQLite database, and records
`rtc_jump_from`/`rtc_jump_to` when the clock is adjusted. It also tracks `low_power_wakeup_count`
(lpw) received from `power_mon` and stores it in the UDID record.

**Process name:** `time_sync`
**Log file:** `/home/ubuntu/.nddevice/log/time_sync/`
**Log format:** `<epoch_ms>: <uptime_ms>: <TAG>: <level>: <pid>: <tid>: <message>`
**Config section:** `[time_sync]` in `bagheera_config.ini` / `bagheera_override.ini`
**UDID DB path:**
- bagheera2/3: `/home/ubuntu/.nddevice/udid.db`
- krait/krait2: `/data/nd_files/.nddevice/udid.db`
**Token file:** `/dev/shm/nd_files_c/time_sync_token_file.bin` (bagheera), `/dev/shm/time_sync_token_file.bin` (krait)
**MSGQ path:** `/dev/shm/MSGQ/TIME_SYNC`

---

## Log Format

```
<epoch_ms>: <uptime_ms>: <TAG>: <level>: <pid>: <tid>: <message>
```

Key tags:
- `TIME_SYNC:` — main service logic (config, GPS/network sync, UDID, health payload)
- `TIME_U:` — time utility: system clock set, RTC hardware set
- `UDID:` — drive ID management, RTC-jump recording, DB queries
- `MSGQ:` / `MSGQ_U:` — message queue setup
- `CFG_PRSR:` — config file parsing at startup
- `FILE_U:` — token file creation

---

## Service Flows

### Flow 1: Service Initialization & Config Parsing

**What happens:** At startup, `time_sync` parses `bagheera_override.ini` then
`bagheera_config.ini` for three keys: `enabled`, `gps_time_sync_enable`, and
`network_time_sync_enable`. Each resolved value triggers a log line. The service then creates
the message queue, spawns the network-time thread, and subscribes for GPS data.

**When active:** Every service start / reboot
**Frequency:** Once per start

**Key log patterns:**
```
CFG_PRSR: ... Override file /home/ubuntu/config/bagheera_override.ini present
CFG_PRSR: ... OVerride file parsed successfully
CFG_PRSR: ... No value present for key enabled in override dictionary
TIME_SYNC: ... Time sync feature is enabled in config file
CFG_PRSR: ... No value present for key gps_time_sync_enable in override dictionary
TIME_SYNC: ... Time sync from GPS is enabled in config file
CFG_PRSR: ... No value present for key network_time_sync_enable in override dictionary
TIME_SYNC: ... Time sync from network is enabled in config file
```

**When GPS or network sync is disabled via config:**
```
TIME_SYNC: ... Time sync from GPS is disabled in config file
TIME_SYNC: ... Time sync from network is disabled in config file
```

**Test cases that validate this flow:**
| Test Case ID    | pytest Path                                                               | What it checks                                             | Related Bugs |
| --------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------- | --- |
| `TC_358`        | `tests/timesync/test_tc_timesync_358_default_config_and_log_parsing.py`   | Config keys read + all 3 "enabled" log lines present       | — |
| `TC_380`        | `tests/timesync/test_tc_timesync_380_validation_when_disabled.py`         | Service disabled via systemctl — other services unaffected | — |

---

### Flow 2: Message Queue Creation

**What happens:** After config parsing, the service clears any stale MSGQ entry, creates a
new server-side message queue named `TIME_SYNC`, and logs the ipc key and token. A file-system
path `/dev/shm/MSGQ/TIME_SYNC` is also created.

**When active:** Every service start
**Frequency:** Once per start

**Key log patterns:**
```
MSGQ: ... Cleared message Q
MSGQ: ... Message queue server created TIME_SYNC <ipc_key> <token> 22
TIME_SYNC: ... Message queue created
TIME_SYNC: ... Spawning thread to check network time every minute
TIME_SYNC: ... subscribed for GPS data
```

**Test cases that validate this flow:**
| Test Case ID    | pytest Path                                                               | What it checks                                             | Related Bugs |
| --------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------- | --- |
| `TC_359`        | `tests/timesync/test_tc_timesync_359_msgq_creation.py`                    | MSGQ cleared + server created + `TIME_SYNC` path exists    | — |

---

### Flow 3: UDID Increment & Token File on Boot

**What happens:** On every boot, `time_sync` reads the last UDID from `udid.db`, increments
it by 1, inserts the new row (`INSERT INTO UDID_TABLE(UDID,BOOT_TIME) ... WHERE NOT EXISTS`),
sets `first_after_boot = true`, creates `time_sync_token_file.bin` in `/dev/shm`, and logs
`udid_global <N>`. Restarting the service (without reboot) does NOT increment the UDID.

**UDID DB path by device type:**
| Device Type    | UDID DB Path                                |
| -------------- | ------------------------------------------- |
| bagheera2/3    | `/home/ubuntu/.nddevice/udid.db`            |
| krait / krait2 | `/data/nd_files/.nddevice/udid.db`          |

**Read current UDID from DB:**
```bash
# bagheera:
sqlite3 /home/ubuntu/.nddevice/udid.db "SELECT MAX(UDID) FROM UDID_TABLE;"
# krait:
sqlite3 /data/nd_files/.nddevice/udid.db "SELECT MAX(UDID) FROM UDID_TABLE;"
```

**When active:** On every device reboot only (not service restart)
**Frequency:** Once per reboot

**Key log patterns:**
```
UDID: ... udid_node.index <prev>, udid_node.udid <prev> udid_json {...}
UDID: ... first_after_boot is true udid is <new_udid>
UDID: ... udid_global <new_udid>
UDID: ... success in create_table_db
FILE_U: ... Created file: /dev/shm/nd_files_c/time_sync_token_file.bin
```

**Test cases that validate this flow:**
| Test Case ID    | pytest Path                                                               | What it checks                                             | Related Bugs |
| --------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------- | --- |
| `TC_362`        | `tests/timesync/test_tc_timesync_362_udid_increment_after_reboot.py`      | UDID = prev + 1 after reboot; DB exists; token file logged | `BG4-761`, `OCTO-2002` |
| `TC_377`        | `tests/timesync/test_tc_timesync_377_restart_and_check_udid.py`           | UDID unchanged after `systemctl restart time_sync`         | — |

---

### Flow 4: GPS Time Sync (~15s after boot)

**What happens:** Once GPS provides a valid fix, `time_sync` receives a GPS data message on
its subscription socket, validates the timestamp, calculates `proc_delay`, sets the system
clock via `settimeofday`, then sets the hardware RTC via `hwclock`. The RTC jump
(`rtc_jump_from` → `rtc_jump_to`) is stored in the UDID JSON. The service also sends a
`TIME_SYNC_RTC_JUMP` message to nd-central (`ndcentral`) and attempts (non-fatally) to notify
`AWSIOT_PUB`.

**Timing observed in device log:** GPS sync occurs ~15–17 seconds after service start
(`uptime_ms` ≈ 15000–17000 ms from startup).

**GPS check interval:** `GPS is not valid; returning from gps_hs_data` is logged at ≥30s
intervals when GPS is unavailable (validates TC_361).

**When active:** When `gps_time_sync_enable = true` (default) and GPS fix obtained
**Frequency:** Once per boot on GPS fix; re-sync if drift detected

**Key log patterns:**
```
TIME_SYNC: ... GPS timestamp: <epoch_ms>, curr timestamp: <epoch_ms>
TIME_SYNC: ... Timestamp ts : <epoch_ms>, prev_time : <epoch_ms>, time_source : 0
TIME_SYNC: ... Recent GPS timestamp: YYYY-MM-DD HH:MM:SS
TIME_SYNC: ... Recent GPS timestamp: <epoch_ms>, Recent Network timestamp: 0
TIME_SYNC: ... system time <epoch_ms>, prev system time: <epoch_ms>, curr system time: <epoch_ms>, proc_delay: 2
TIME_SYNC: ... Setting system time <epoch_ms>
TIME_U: ... Set system time successful
TIME_SYNC: ... System time set by GPS
TIME_U: ... Setting RTC clock to : Year: <Y>, Month: <M>, Date: <D>, Hour: <H>, Minute: <M>, Second: <S>
TIME_U: ... HW time set successful
TIME_U: ... Output of hwclock show command is \n: YYYY-MM-DD HH:MM:SS.mmm+0000
UDID: ... Updating rtc jump details, system_time_updated_from: <from_ms>, system_time_updated_to: <to_ms>
UDID: ... TIME_SYNC_RTC_JUMP message Send to ndcentral: <from_ms>, <to_ms>
```
**AWSIOT_PUB notification failure (non-fatal, normal):**
```
MSGQ: E: ... Error creating token AWSIOT_PUB 15, Error msg: No such file or directory
UDID: E: ... sending TIME_SYNC_RTC_JUMP msg to awsiot failed
```

**When GPS is unavailable (every ≥30s):**
```
UDID: ... GPS is not valid; returning from gps_hs_data
```

**Test cases that validate this flow:**
| Test Case ID    | pytest Path                                                               | What it checks                                             | Related Bugs |
| --------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------- | --- |
| `TC_361`        | `tests/timesync/test_tc_timesync_361_gps_check_every_30sec.py`            | `GPS is not valid` interval ≥30000ms between entries       | — |
| `TC_371`        | `tests/timesync/test_tc_timesync_371_gps_time_sync_validation.py`         | Full GPS sync chain: GPS fix → system time set → HW RTC    | `DT-4222` |
| `TC_372`        | `tests/timesync/test_tc_timesync_372_lte_and_gps_time_sync_after_reboot.py` | GPS sync or LTE sync (at least one must succeed)          | `DT-4222` |
| `TC_376`        | `tests/timesync/test_tc_timesync_376_lte_gps_time_sync_lpw.py`            | GPS/LTE sync after LPW cycle + past-time set               | `OCTO-2149`, `OCTO-2002` |
| `TC_379`        | `tests/timesync/test_tc_timesync_379_set_time_to_future.py`               | GPS/LTE corrects future-time after reboot                  | `BG4-761`, `OCTO-2149` |
| `TC_382`        | `tests/timesync/test_tc_timesync_382_no_gps_lte_timesync_behaviour.py`    | `GPS is not valid` log present when GPS disabled           | — |

---

### Flow 5: LTE / Network Time Sync (~60s via AT+CCLK)

**What happens:** The network-time thread spawns every ~60 seconds. It first checks LTE
connectivity by running `lte_gps_sample_app 'at+cgact?'` in a child process. If LTE is active
(`+CGACT: 1,1`), it fires `LTE_CONNECTIVITY_UPDATE received`, then spawns a second child
process that runs `curl -sI www.google.com | grep "Date:"` (to get the year) then
`lte_gps_sample_app 'at+cclk?'` to get the full timestamp. The response is parsed:
`+cclk: "YY/MM/DD,HH:MM:SS-TZ"`. The EPOCH is computed and compared against `time_source: 1`
(network). A health payload is sent after LTE sync.

**LTE connectivity check command:**
```bash
lte_gps_sample_app 'at+cgact?'
# Expected response indicating LTE active:
+CGACT: 1,1
```

**Time acquisition command:**
```bash
curl -sI www.google.com | grep "Date:" | awk '{print $5}'    # → year
lte_gps_sample_app 'at+cclk?'                                 # → +cclk: "26/05/21,10:39:12-28"
```

**When LTE is not available:**
```
TIME_SYNC: ... LTE connectivity do not exist
```

**When active:** When `network_time_sync_enable = true` (default) and LTE is available
**Frequency:** Every ~60 seconds

**Key log patterns:**
```
TIME_SYNC: ... AT command run child process for lte conn check launched with pid = <PID>
TIME_SYNC: ... AT command to get network time process launched
TIME_SYNC: ... Modem Output : modem_response: \n\nat+cgact?\n\n+CGACT: 1,1\n+CGACT: 3,1\n\nOK
TIME_SYNC: ... LTE_CONNECTIVITY_UPDATE received
TIME_SYNC: ... AT command run child process  to get network time launched with pid = <PID>
TIME_SYNC: ... Curl Get Year response : <YYYY>
TIME_SYNC: ... Modem Output : modem_response: \n\nat+cclk?\n\n+cclk: "YY/MM/DD,HH:MM:SS-TZ"\n\nOK
TIME_SYNC: ... Curl Year : <YYYY>,  cclk Year : <YYYY>
TIME_SYNC: ... Year: <Y>, Month: <M>, Date: <D>, Hour: <H>, Minute: <M>, Sec: <S>
TIME_SYNC: ... EPOCH time corresponding to the obtained network time: <epoch_ms>
TIME_SYNC: ... Time taken to run at command = <N> ms
TIME_SYNC: ... AT command run child process 8556 to get network time successfully completed
TIME_SYNC: ... NETWORK_TIME_UPDATE received
TIME_SYNC: ... Timestamp ts : <epoch_ms>, prev_time : <epoch_ms>, time_source : 1
TIME_SYNC: ... Recent NETWORK timestamp: YYYY-MM-DD HH:MM:SS
TIME_SYNC: ... Recent GPS timestamp: <epoch_ms>, Recent Network timestamp: <epoch_ms>
TIME_SYNC: ... Time difference between new time source and current system time: <N> ms
TIME_SYNC: ... Health payload sent
```

**Test cases that validate this flow:**
| Test Case ID    | pytest Path                                                               | What it checks                                             | Related Bugs |
| --------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------- | --- |
| `TC_360`        | `tests/timesync/test_tc_timesync_360_network_based_time_sync.py`          | LTE thread spawned, AT cmd launched, LTE connectivity log  | — |
| `TC_366`        | `tests/timesync/test_tc_timesync_366_lte_time_sync_after_reboot.py`       | Full LTE sync chain: conn check → modem → CCLK → epoch     | — |
| `TC_372`        | `tests/timesync/test_tc_timesync_372_lte_and_gps_time_sync_after_reboot.py` | GPS or LTE sync succeeds after reboot                    | `DT-4222` |
| `TC_376`        | `tests/timesync/test_tc_timesync_376_lte_gps_time_sync_lpw.py`            | LTE sync available after LPW cycle                         | `OCTO-2149`, `OCTO-2002` |
| `TC_379`        | `tests/timesync/test_tc_timesync_379_set_time_to_future.py`               | LTE corrects future time after reboot                      | `BG4-761`, `OCTO-2149` |
| `TC_382`        | `tests/timesync/test_tc_timesync_382_no_gps_lte_timesync_behaviour.py`    | `LTE connectivity do not exist` when invalid APN set       | — |

---

### Flow 6: Low Power Wakeup Count (lpw) Propagation

**What happens:** After a low-power wakeup cycle (ignition OFF → LPW → ignition ON),
`power_mon` sends the `low_power_wakeup_count` to `time_sync`. The service logs:
`Received low_power_wakeup_cnt: <N>` and stores the value in the UDID record's
`"low_power_wakeup_count"` JSON field. Observed values: `0` (no LPW), `1` (one LPW cycle),
`2` (two LPW cycles).

**When active:** Always (power_mon sends on every boot)
**Frequency:** Once per boot, soon after service start (~3s)

**Key log patterns:**
```
TIME_SYNC: ... Received low_power_wakeup_cnt: <N>
```
**In UDID JSON (from `UDID:` log lines):**
```json
{"drive_start": ..., "low_power_wakeup_count": 2, ...}
```
**In power_mon logs (cross-reference):**
```
power_mon: ... sending lpw_cnt: <N> to time_sync
```

**Test cases that validate this flow:**
| Test Case ID    | pytest Path                                                               | What it checks                                             | Related Bugs |
| --------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------- | --- |
| `TC_374`        | `tests/timesync/test_tc_timesync_374_lpw_count_zero_to_udid.py`           | power_mon sends 0, time_sync receives 0, udid.db = 0       | — |
| `TC_375`        | `tests/timesync/test_tc_timesync_375_lpw_count_one.py`                    | After 1 and 2 LPW cycles, lpw_cnt 1 and 2 received        | — |
| `TC_376`        | `tests/timesync/test_tc_timesync_376_lte_gps_time_sync_lpw.py`            | GPS/LTE sync works after LPW cycle                         | `OCTO-2149`, `OCTO-2002` |

---

### Flow 7: Drive Time Updation (every ~30s)

**What happens:** The UDID record's `drive_end` is updated every ~30 seconds by the health
monitoring thread. `drive_start` is fixed at the boot timestamp and never changes during a
session. `rtc_jump_from` and `rtc_jump_to` record the pre- and post-sync clock values when
GPS sets the time.

**Expected behavior:**
- `drive_start`: constant (boot time epoch, e.g., `1779359872000`)
- `drive_end`: increases by ~30000ms every ~30s (±2s tolerance)

**Verification commands:**
```bash
# bagheera:
sqlite3 /home/ubuntu/.nddevice/udid.db "SELECT drive_start, drive_end FROM UDID_TABLE WHERE UDID=(SELECT MAX(UDID) FROM UDID_TABLE);"
# Or parse from log:
grep "new json" /home/ubuntu/.nddevice/log/time_sync/* | tail -2
```

**When active:** Always (per-session)
**Frequency:** Every ~30 seconds

**Test cases that validate this flow:**
| Test Case ID    | pytest Path                                                               | What it checks                                             | Related Bugs |
| --------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------- | --- |
| `TC_367`        | `tests/timesync/test_tc_timesync_367_drive_time_updation_check.py`        | drive_start constant; drive_end increases 28000–62000ms    | — |

---

### Flow 8: NTP / System Time Daemon Disabled

**What happens:** The device must NOT be using OS-level NTP daemons — time sync is exclusively
managed by `time_sync` via GPS and LTE. On different device types:
- **bagheera2/3**: `systemd-timesyncd` must be **inactive**
- **krait/krait2**: `chronyd` must be **inactive**
- **octo**: NTP client must be **inactive**

**Check commands by device type:**
```bash
# bagheera:
systemctl is-active systemd-timesyncd 2>/dev/null | grep -qi inactive && echo true
# krait:
systemctl is-active chronyd 2>/dev/null | grep -qi inactive && echo true
# octo:
systemctl is-active ntp 2>/dev/null | grep -qi inactive && echo true
```

**When active:** Always (static property of the OS config)
**Frequency:** Checked post-reboot

**Test cases that validate this flow:**
| Test Case ID    | pytest Path                                                               | What it checks                                             | Related Bugs |
| --------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------- | --- |
| `TC_369`        | `tests/timesync/test_tc_timesync_369_ntp_disabled_by_default.py`          | OS NTP daemon is inactive on all device types              | — |

---

### Flow 9: Log Directory Presence

**What happens:** The time_sync log directory must exist and be writable.
**Log dir path:** `/home/ubuntu/.nddevice/log/time_sync/` (bagheera/octo),
`/data/nd_files/.nddevice/log/time_sync/` (krait)

**Test cases that validate this flow:**
| Test Case ID    | pytest Path                                                               | What it checks                                             | Related Bugs |
| --------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------- | --- |
| `TC_370`        | `tests/timesync/test_tc_timesync_370_log_presence.py`                     | `ls <log_dir>` succeeds                                    | — |

---

### Flow 10: Bootup Timing Comparison

**What happens:** `time_sync`, `power_mon`, and `bagheera` all start within 30 seconds of
each other after boot. This is verified by comparing the epoch timestamps of their first log
entry. Any service starting more than 30s after `time_sync` indicates a delayed boot or
service-start failure.

**When active:** Post-reboot
**Tolerance:** ≤30000ms between `time_sync` start and any other service start

**Test cases that validate this flow:**
| Test Case ID    | pytest Path                                                               | What it checks                                             | Related Bugs |
| --------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------- | --- |
| `TC_381`        | `tests/timesync/test_tc_timesync_381_bootup_time_compare.py`              | time_sync, power_mon, bagheera start within 30s of each other | `OCTO-2002` |

---

### Flow 11: Delayed Service Start Behavior

**What happens:** If `time_sync` is stopped for 140s after boot, then started manually,
the service must initialize normally. Other services (bagheera, power_monitor,
HealthStatsManager) must have been running for ≥2 minutes before `time_sync` starts —
verifying they don't depend on `time_sync` being present immediately.

**When active:** Test scenario (stop → wait 140s → start)

**Test cases that validate this flow:**
| Test Case ID    | pytest Path                                                               | What it checks                                             | Related Bugs |
| --------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------- | --- |
| `TC_378`        | `tests/timesync/test_tc_timesync_378_delayed_service_behaviour.py`        | Other services run ≥2min before time_sync starts           | — |

---

### Flow 12: Future / Past Time Correction

**What happens:** When the system clock is set to a future date (`30 JUN 2027`) or a past
date (`22 FEB 2024`) before reboot, `time_sync` must correct it after boot using GPS or LTE.
The correction is validated by checking that `Set system time successful` and
`HW time set successful` appear after the reboot timestamp.

**Time manipulation commands (test setup):**
```bash
# bagheera (future):
sudo date -s '30 JUN 2027 11:14:00'; sudo hwclock -w
# bagheera (past):
sudo date -s '22 FEB 2024 11:14:00'; sudo hwclock -w
# krait (future):
date -s '30 JUN 2027 11:14:00'; hwclock -w
```

**When active:** After manual clock manipulation + reboot
**Cross-service impact:** GPS or LTE must be available to correct the clock

**Test cases that validate this flow:**
| Test Case ID    | pytest Path                                                               | What it checks                                             | Related Bugs |
| --------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------- | --- |
| `TC_379`        | `tests/timesync/test_tc_timesync_379_set_time_to_future.py`               | GPS or LTE corrects future clock after reboot              | `BG4-761`, `OCTO-2149` |
| `TC_376`        | `tests/timesync/test_tc_timesync_376_lte_gps_time_sync_lpw.py`            | GPS or LTE corrects past clock after LPW cycle + reboot    | `OCTO-2149`, `OCTO-2002` |

---

### Flow 13: Service Disabled — No Impact on Other Services

**What happens:** When `time_sync` is disabled (`systemctl disable time_sync`) and the device
is rebooted, the other key services (bagheera, power_monitor, HealthStatsManager) must still
be running and have uptime ≥2 minutes after 160 seconds. This verifies `time_sync` is not
in the critical boot path for other services.

**When active:** Negative test scenario (time_sync disabled)
**Postcondition:** Re-enable and restart time_sync

**Test cases that validate this flow:**
| Test Case ID    | pytest Path                                                               | What it checks                                             | Related Bugs |
| --------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------- | --- |
| `TC_380`        | `tests/timesync/test_tc_timesync_380_validation_when_disabled.py`         | bagheera/power_monitor/HealthStatsManager run ≥2min without time_sync | — |

---

### Flow 14: No GPS + No LTE — Graceful Degradation

**What happens:** When both GPS is disabled (no valid fix) and LTE is unavailable (invalid APN
configured), `time_sync` must log appropriate unavailability messages and not crash. The service
continues running and retries on its normal intervals.

**GPS disabled log:**
```
UDID: ... GPS is not valid; returning from gps_hs_data
```
**LTE unavailable log:**
```
TIME_SYNC: ... LTE connectivity do not exist
```

**LTE disable method (test setup):** Set invalid APN in `conn_mgr_config.txt`, reboot,
disable GPS subscription. Restore via:
```bash
systemctl enable conn_mgr
systemctl start conn_mgr
```

**When active:** When GPS unavailable AND LTE unavailable simultaneously
**Frequency:** Event-driven (test scenario / tunnel / no-signal area)

**Test cases that validate this flow:**
| Test Case ID    | pytest Path                                                               | What it checks                                             | Related Bugs |
| --------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------- | --- |
| `TC_382`        | `tests/timesync/test_tc_timesync_382_no_gps_lte_timesync_behaviour.py`    | Both `GPS is not valid` + `LTE connectivity do not exist` logged | — |

---

## Config-Driven Flow Activation

The agent MUST read device config from `device_data/device_<ID>_config.ini` before selecting flows:

| Config Section  | Config Key                    | Value       | Activates Flow(s)                        | Test Cases                     |
| --------------- | ----------------------------- | ----------- | ---------------------------------------- | ------------------------------ |
| `[time_sync]`   | `enabled`                     | `true`/`1`  | Flows 1–14 (all flows, service runs)     | TC_358, all                    |
| `[time_sync]`   | `enabled`                     | `false`/`0` | Flow 13: Disabled service behavior       | TC_380                         |
| `[time_sync]`   | `gps_time_sync_enable`        | `true`/`1`  | Flow 4: GPS Time Sync                    | TC_361, TC_371, TC_372, TC_376 |
| `[time_sync]`   | `gps_time_sync_enable`        | `false`/`0` | GPS sync disabled; GPS log = unavailable | TC_382                         |
| `[time_sync]`   | `network_time_sync_enable`    | `true`/`1`  | Flow 5: LTE/Network Time Sync            | TC_360, TC_366, TC_372, TC_376 |
| `[time_sync]`   | `network_time_sync_enable`    | `false`/`0` | LTE sync disabled                        | —                              |
| —               | —                             | always      | Flow 2: MSGQ creation                    | TC_359                         |
| —               | —                             | always      | Flow 3: UDID increment (on reboot)       | TC_362, TC_377                 |
| —               | —                             | always      | Flow 6: LPW count propagation            | TC_374, TC_375                 |
| —               | —                             | always      | Flow 7: Drive time update                | TC_367                         |
| —               | —                             | always      | Flow 8: NTP disabled check               | TC_369                         |

**Default values** (when key absent from config):
- `enabled` → `true`
- `gps_time_sync_enable` → `true`
- `network_time_sync_enable` → `true`

---

## Cross-Service Dependencies

| Related Service        | Why                                                                      | When to check its logs              |
| ---------------------- | ------------------------------------------------------------------------ | ----------------------------------- |
| `gps`                  | Provides GPS data subscription (`GPS_DATA` socket); GPS fix status       | Flow 4 (TC_371, TC_361)             |
| `power_mon`            | Sends `low_power_wakeup_count` to time_sync via IPC                      | Flow 6 (TC_374, TC_375)             |
| `conn_mgr`             | Manages LTE connectivity; invalid APN blocks LTE time sync               | Flow 5, 14 (TC_360, TC_382)         |
| `bagheera` (ndcentral) | Receives `TIME_SYNC_RTC_JUMP` message when GPS adjusts the clock         | Flow 4 (RTC jump cross-service)     |
| `AWSIOT_PUB`           | Receives RTC jump notification (non-fatal if AWSIOT not running)         | Flow 4 (error is normal)            |
| `HealthStatsManager`   | Uses system time from time_sync for session timestamps                   | Flow 13 (TC_380), Flow 11 (TC_378)  |
| `systemd-timesyncd` / `chronyd` | Must be INACTIVE — time_sync is the sole time authority         | Flow 8 (TC_369)                     |

---

## Flow Dependency Graph

```
boot → [Flow 1: Config Parsing] → [Flow 2: MSGQ Creation]
     → [Flow 3: UDID Increment + token file] — once per reboot
     → [Flow 6: LPW count from power_mon] — ~3s after start
     → [Flow 4: GPS Time Sync] — ~15s after start (if GPS fix obtained)
         → RTC set → UDID rtc_jump_from/to updated → ndcentral notified
     → [Flow 5: LTE Time Sync] — ~60s thread, every minute
         → AT+CGACT? → AT+CCLK? → EPOCH → health payload
     → [Flow 7: Drive Time Update] — every ~30s (UDID drive_end++)

delayed-start → [Flow 11: Delayed Service Behavior]
disabled → [Flow 13: Service Disabled]
future/past clock → [Flow 12: Clock Correction] → GPS/LTE corrects after reboot
no GPS + no LTE → [Flow 14: Graceful Degradation]
LPW cycle → [Flow 6: LPW Count] → [Flow 4/5: Time Sync after LPW]
```

---

## Validation Instructions for the Agent

1. **Device type** determines DB and token file paths — read from `device_data/device_<ID>_config.ini`
2. **Log file**: `/home/ubuntu/.nddevice/log/time_sync/` — may contain multiple rotated files; search all
3. **Log timestamp format**: `<epoch_ms>: <uptime_ms>: ...` — use `<epoch_ms>` field for `since_ts` filtering
4. **For Flow 3 (UDID increment)**: query DB directly: `sqlite3 <db_path> "SELECT MAX(UDID) FROM UDID_TABLE;"`; compare to value logged as `first_after_boot is true udid is <N>`
5. **For Flow 4 (GPS sync)**: validate chain: `GPS timestamp` → `Setting system time` → `Set system time successful` → `System time set by GPS` → `HW time set successful`; `time_source : 0` = GPS
6. **For Flow 5 (LTE sync)**: validate chain: `AT command run child process for lte conn check` → `LTE_CONNECTIVITY_UPDATE received` → `AT command run child process to get network time` → `NETWORK_TIME_UPDATE received`; `time_source : 1` = network
7. **For Flow 6 (LPW)**: check `Received low_power_wakeup_cnt: <N>` in time_sync log and `"low_power_wakeup_count": <N>` in UDID JSON; cross-check `power_mon` log for send event
8. **For Flow 7 (drive_end)**: two consecutive `new json` entries; `drive_start` must be identical; `drive_end` diff must be 28000–62000ms
9. **For Flow 8 (NTP disabled)**: command is device-type-specific — see config table above
10. **For Flow 12 (future/past correction)**: `since_ts` must be set to the epoch of the reboot — `Set system time successful` must appear AFTER that epoch
11. **AWSIOT_PUB error is non-fatal**: `sending TIME_SYNC_RTC_JUMP msg to awsiot failed` is expected and normal when AWSIOT is not yet running
12. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED / SKIPPED
