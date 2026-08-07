---
name: svc-service-validation
description: "Use when: running SVC (Supervisor Controller) service validation test cases on Netradyne devices. Covers initialization/config parsing, MSGQ creation, watchdog (AON/PMIC), keepalive registration and timeouts, keepalive post-restart, config file recovery (missing/zero-size), config backup on boot, disk monitoring, button press detection, MSP/QCS alive signal (krait), low-power wakeup behavior, and locale config file critical events."
argument-hint: "device serial (e.g., /svc-service-validation 103452403525)"
---

# SVC (Supervisor Controller) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the SVC service —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads test cases for actual log patterns,
> device-type paths, and acceptance criteria — this skill does NOT duplicate those.

---

## Service Overview

`svc` is a critical system supervisor service that starts before all application services and remains running for the lifetime of the device session. It manages hardware watchdog timers (PMIC and AON WDT), monitors keepalive heartbeats from application services (bagheera, power_monitor, apm, awsiot, analyticsService) and triggers a device reboot if any monitored service stops responding within its timeout window. It also runs a background recovery thread that checks config file integrity every 900 seconds and restores from backup if a file is missing or zero-size. Additional responsibilities include disk space monitoring every 180 seconds, physical button press detection and forwarding to ndcentral, MSP/QCS alive signal management on krait devices, and locale config file status reporting.

**Process name:** `svc`
**Log file:** `svc.log`
**Log paths by device type:**
- bagheera2 / bagheera3 / octo: `/home/ubuntu/.nddevice/log/`
- krait / krait2: `/data/nd_files/log/`

**Message queue:** `/dev/shm/MSGQ/Q_SVC`
**Primary config sections:** `[svc]`, `[watchdog]`, `[diskmon]`, `[recovery]`

---

## Log Format

```
<epoch_ms>: <uptime_ms>: <TAG>: <level>: <pid>: <tid>: <message>
```

**Tags observed in svc.log:**

| Tag | Module |
|-----|--------|
| `SVC:` | Main SVC service logic |
| `RCVRY:` | Config recovery / backup thread |
| `DISK:` | Disk monitoring thread |
| `WDOG:` | Watchdog initialization |
| `PMIC_WDOG:` | PMIC watchdog kick |
| `DEVB3:` | bagheera3-specific device ops (AON WDT, PMIC config) |
| `MSGQ:` | Message queue server creation |
| `CFG_PRSR:` | Config parser (override file reads) |
| `SYS_U:` | Shell command execution |
| `FILE_U:` | File utility operations |
| `DB_U:` | DB operations |

**Example startup lines (device 103452403525, PID 5987):**
```
1779359889856: 232: SVC: I: 5987: 5987: ######Starting SVC######
1779359890076: 452: SVC: I: 5987: 5987: read config_recovery_poll: 900
1779359890076: 452: SVC: I: 5987: 5987: config_recovery_poll: 900
1779359890240: 616: SVC: I: 5987: 5987: read diskmon_poll: 180
1779359890240: 616: SVC: I: 5987: 5987: diskmon_poll: 180
1779359890278: 654: SVC: I: 5987: 5987: read watchdog_timeout from config in secs: 90
1779359890279: 655: SVC: I: 5987: 5987: watchdog_timeout: 90
1779359890329: 705: SVC: I: 5987: 5987: read svc_failure_case_timeout from config in secs: 300
1779359890329: 705: SVC: I: 5987: 5987: svc_failure_case_timeout: 300
1779359890330: 706: SVC: I: 5987: 5987: Recovery enabled: 1, Diskmon enabled: 1
1779359890372: 748: SVC: I: 5987: 5987: inst_long_press_duration_ms 10000
1779359890372: 748: SVC: I: 5987: 5987: long_press_duration_ms: 5000
```

---

## Service Flows

### Flow 1: Initialization & Config Parsing

**What happens:** SVC reads all config parameters at startup. It reads `config_recovery_poll` (default 900s), `diskmon_poll` (default 180s), `watchdog_timeout` (default 90s), `svc_failure_case_timeout` (default 300s), `inst_long_press_duration_ms` (default 10000ms), and `long_press_duration_ms` (default 5000ms). Override config (`bagheera_override.ini`) is applied where present. After parsing, SVC logs the resolved values and initializes MSGQ, watchdog, diskmon, and recovery threads.

**When active:** Always at every boot
**Frequency:** Once at boot
**Cross-service impact:** Parameters set here govern all other flows

**Key log patterns:**
```
######Starting SVC######
read config_recovery_poll: 900
config_recovery_poll: 900
read diskmon_poll: 180
diskmon_poll: 180
read watchdog_timeout from config in secs: 90
watchdog_timeout: 90
read svc_failure_case_timeout from config in secs: 300
svc_failure_case_timeout: 300
Recovery enabled: 1, Diskmon enabled: 1
inst_long_press_duration_ms 10000
long_press_duration_ms: 5000
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_svc_289` | `tests/svc/test_tc_svc_289_config_file_checksum_match.py` | Config file checksums match at init, "Init recovery" logged | — |

---

### Flow 2: MSGQ Server Creation

**What happens:** SVC creates a POSIX message queue server named `Q_SVC` in `/dev/shm/MSGQ/`. The MSGQ is the IPC endpoint through which other services send keepalive and command messages to SVC. Creation is logged with queue ID and parameters. After creation SVC logs "Message queue created".

**When active:** Always at every boot
**Frequency:** Once at boot
**Cross-service impact:** All keepalive-sending services (bagheera, power_monitor, apm, awsiot, analyticsService) must connect to this queue

**Key log patterns:**
```
MSGQ: I: <pid>: <pid>: Message queue server created Q_SVC 491531 319986068 19
SVC: I: <pid>: <pid>: Message queue created
```
File created: `/dev/shm/MSGQ/Q_SVC`

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_svc_298` | `tests/svc/test_tc_svc_298_message_queue_server_creation.py` | "Message queue server created Q_SVC" in logs + `/dev/shm/MSGQ/Q_SVC` file exists | — |

---

### Flow 3: Watchdog Initialization (AON WDT + PMIC)

**What happens:** SVC initializes the hardware watchdog immediately after MSGQ creation. On bagheera3 and octo, it enables the AON (Always-On) WDT with a 600-second timeout and configures the PMIC watchdog timer to 128 seconds. On bagheera2, only PMIC watchdog is engaged (128s). SVC then starts a periodic kick thread that sends `i2cset` commands to reset the PMIC watchdog counter every ~30 seconds. If SVC stops running (crash, kill), the watchdog fires and reboots the device. On krait/krait2, no hardware watchdog is used; SVC relies on keepalive timeout for self-reboot (TC_304).

**When active:** Always at every boot on bagheera2/3/octo; krait/krait2 skip hardware watchdog
**Frequency:** Init once; PMIC kick every ~30s
**Cross-service impact:** SVC death → watchdog fires → device reboots (bagheera); SVC self-triggers reboot after keepalive timeout on krait

**Key log patterns:**
```
WDOG: I: <pid>: <pid>: Initialized watchdog
DEVB3: I: <pid>: <pid>: Configuring PMIC for reboot
DEVB3: I: <pid>: <pid>: Engaging PMIC WDT timer to 128s
DEVB3: I: <pid>: <pid>: AON WDT State: 1
WDT is set to 600.
DEVB3: I: <pid>: <pid>: AON WDT Enabled, WDT State: 1, TIMEOUT: 600s
SVC: I: <pid>: <pid>: init_watchdog success
PMIC_WDOG: I: <pid>: <pid>: trigger_kick_pmic_wdog return status 0
PMIC_WDOG: I: <pid>: <pid>: Kicking PMIC watchdog
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_svc_291` | `tests/svc/test_tc_svc_291_aon_wdt.py` | "Initialized watchdog" logged; on bagheera3/octo "AON WDT Enabled, WDT State: 1, TIMEOUT: 600s" present | `DT-3817` |
| `TC_svc_292` | `tests/svc/test_tc_svc_292_pmic_watchdog_init.py` | PMIC watchdog kick interval 29000–31000ms (bagheera2/3/octo only) | — |
| `TC_svc_363` | `tests/svc/test_tc_svc_363_watchdog_trigger_reboot.py` | Device reboots after SVC is killed (max 660s krait/krait2, 200s bagheera/octo) | — |

---

### Flow 4: Keepalive Registration & Timeout Settings

**What happens:** After recovery init completes, SVC registers keepalive monitoring for each application service with its configured timeout. On this device (bagheera3), the observed timeouts are: bagheera=120s, awsiot=120s, apm=300s, power_monitor=300s, analyticsService=180s. AWSIOT keepalive registration at startup is explicitly ignored ("Ignoring keep_alive_at_start for service awsiot") — it is only tracked after it first registers. For each other service, SVC logs "registering keep_alive_at_start for service <name>" and begins timing from the first received keepalive.

**When active:** Always — runs after recovery init completes at boot
**Frequency:** Registration once at boot; timeout monitoring runs continuously
**Cross-service impact:** All monitored services must send keepalives within their timeout or SVC triggers a reboot

**Key log patterns:**
```
SVC: I: <pid>: <pid>: Recovery init status: 1
SVC: I: <pid>: <pid>: service bagheera timeout 120
SVC: I: <pid>: <pid>: service awsiot timeout 120
SVC: I: <pid>: <pid>: service apm timeout 300
SVC: I: <pid>: <pid>: service power_monitor timeout 300
SVC: I: <pid>: <pid>: service analyticsService timeout 180
SVC: I: <pid>: <pid>: registering keep_alive_at_start for service bagheera
SVC: I: <pid>: <pid>: Ignoring keep_alive_at_start for service awsiot
SVC: I: <pid>: <pid>: registering keep_alive_at_start for service apm
SVC: I: <pid>: <pid>: registering keep_alive_at_start for service power_monitor
SVC: I: <pid>: <pid>: registering keep_alive_at_start for service analyticsService
```

Periodic STATS log (every ~30s):
```
SVC: I: <pid>: <pid>: STATS: service: bagheera, mode: 1, health: 1, last_keep_alive: 38639
SVC: I: <pid>: <pid>: STATS: service: awsiot, mode: 0, health: 1, last_keep_alive: 0
SVC: I: <pid>: <pid>: STATS: service: apm, mode: 1, health: 1, last_keep_alive: 64452
SVC: I: <pid>: <pid>: STATS: service: power_monitor, mode: 1, health: 1, last_keep_alive: 63605
SVC: I: <pid>: <pid>: STATS: service: analyticsService, mode: 1, health: 1, last_keep_alive: 35401
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_svc_297` | `tests/svc/test_tc_svc_297_keepalive_reg_and_timeout.py` | bagheera timeout 120s, awsiot timeout 120s, apm timeout 300s, power_monitor timeout 300s; registration logged for bagheera/apm/power_monitor; awsiot explicitly ignored | — |

---

### Flow 5: Keepalive Messages (Normal Operation)

**What happens:** Each monitored service sends periodic keepalive messages to Q_SVC. SVC logs receipt of each keepalive with "Keep alive received from: q_<service>". The `last_keep_alive` value in STATS logs is the uptime_ms at the time of the last received keepalive — used by the test to verify the service sent a keepalive within the expected interval.

**When active:** Always during normal operation
**Frequency:** Per-service: bagheera ~every 60s, power_monitor ~every 30s, apm ~every 30s
**Cross-service impact:** Confirms bagheera, power_monitor, and apm are all running and healthy

**Key log patterns:**
```
SVC: I: <pid>: <tid>: Keep alive received from: q_nd_central
SVC: I: <pid>: <tid>: Keep alive received from: q_power_monitor
SVC: I: <pid>: <tid>: Keep alive received from: q_apm
SVC: I: <pid>: <tid>: Keep alive received from: q_analytics
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_svc_285` | `tests/svc/test_tc_svc_285_keepalive_msg_bagheera.py` | keepalive_check(nd_central) succeeds within expected interval | — |
| `TC_svc_286` | `tests/svc/test_tc_svc_286_keepalive_msg_power_mon.py` | keepalive_check(power_monitor) succeeds | — |
| `TC_svc_287` | `tests/svc/test_tc_svc_287_keepalive_msg_apm.py` | keepalive_check(apm) succeeds (krait, krait2, bagheera3, octo only) | `DT-3723` |

---

### Flow 6: Keepalive Timeout → Device Reboot

**What happens:** If a monitored service does not send a keepalive within its timeout window, SVC logs "Keep alive timeout: <service> diff: <N> Restarting Service" at E level and triggers a device reboot. The diff value is the elapsed time in seconds since the last keepalive. The reboot reason `DBSTATE_SHUTDOWN_SVC:REBOOT` appears in cloud/power_monitor logs. TC_304 tests SVC's own self-reboot: if SVC itself is killed, the watchdog fires (bagheera/octo) or the OS detects the crash (krait), causing a reboot without an SVC log entry.

**When active:** Triggered when a service misses its keepalive window
**Frequency:** Per event
**Cross-service impact:** Causes full device reboot; reboot reason logged by power_monitor/cloud

**Key log patterns:**
```
SVC: E: <pid>: <pid>: Keep alive timeout:  analyticsService diff: 180 Restarting Service
SVC: E: <pid>: <pid>: Keep alive timeout:  bagheera diff: 120 Restarting Service
SVC: E: <pid>: <pid>: Keep alive timeout:  power_monitor diff: 300 Restarting Service
SVC: E: <pid>: <pid>: Keep alive timeout:  apm diff: 300 Restarting Service
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_svc_295` | `tests/svc/test_tc_svc_295_keepalive_timeout_bagheera.py` | "Keep alive timeout: bagheera diff" in svc logs after bagheera killed; device reboots within 2 min | `DT-3834`, `DT-3314`, `BG4-641`, `AN-30339` |
| `TC_svc_302` | `tests/svc/test_tc_svc_302_keepalive_timeout_power_mon.py` | "Keep alive timeout: power_monitor diff" in svc logs after power_monitor killed; device reboots within 5 min | — |
| `TC_svc_303` | `tests/svc/test_tc_svc_303_keepalive_timeout_apm.py` | "Keep alive timeout: apm diff" in svc logs after apm killed (krait, krait2, bagheera3, octo only) | `DT-3723`, `DT-3963`, `DT-3314`, `AN-30339` |
| `TC_svc_304` | `tests/svc/test_tc_svc_304_keepalive_timeout_svc.py` | Device reboots after SVC killed; "previous_shutdown_reason" in power_mon logs | — |
| `TC_svc_356` | `tests/svc/test_tc_svc_356_power_mon_disable_keepalive.py` | Device reboots after both ndcentral and power_monitor killed | `DT-4247`, `OCTO-2107`, `BG4-641` |

---

### Flow 7: Keepalive Timer Reset After Service Restart

**What happens:** When a monitored service crashes and is restarted (by systemd or manually), SVC resets its keepalive timer for that service and begins monitoring again from the new first keepalive. The device does NOT reboot if the service restarts quickly enough (before the timeout fires). SVC logs "Starting keep alive monitoring for <service> service and updating service stats" and "Keep alive received from: q_<service>" upon receipt of the first keepalive after restart.

**When active:** After a monitored service is restarted
**Frequency:** Per event
**Cross-service impact:** Confirms graceful service recovery without triggering a device reboot

**Key log patterns:**
```
SVC: I: <pid>: <tid>: Starting keep alive monitoring for bagheera service and updating service stats
SVC: I: <pid>: <tid>: Starting keep alive monitoring for analyticsService service and updating service stats
SVC: I: <pid>: <tid>: Keep alive received from: q_nd_central
SVC: I: <pid>: <tid>: Service:  q_nd_central,  status:  0 previous_status: 0    0
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_svc_351` | `tests/svc/test_tc_svc_351_keepalive_post_restart_ndcentral.py` | Device uptime increases (no reboot) after bagheera restart; "Keep alive received from: q_nd_central" in svc logs | `DT-3834`, `DT-3314` |
| `TC_svc_352` | `tests/svc/test_tc_svc_352_keepalive_post_restart_powermon.py` | Device uptime increases (no reboot) after power_monitor restart; keepalive_check(power_monitor) succeeds | — |
| `TC_svc_353` | `tests/svc/test_tc_svc_353_keepalive_post_restart_apm.py` | Device uptime increases (no reboot) after apm restart (krait, krait2, bagheera3, octo only) | — |

---

### Flow 8: Config Recovery Thread (Missing / Zero-Size Files)

**What happens:** SVC's recovery thread initializes at boot by creating recovery objects for each monitored config file (nddevice.ini, deviceconfig.ini, cloudconfig.ini, nd_config_*.ini locale files, nd_config_recovery.ini, bagheera_config.ini). For files with a backup counterpart, it computes md5sums and takes a backup if the current backup is stale. After init, the recovery thread runs a sanity check loop every 900 seconds. If a config file is found missing ("File not present: <path>") or zero-size ("File size Zero: <path>"), SVC triggers primary recovery from the backup directory. On success: "Primary recovery for file: /home/ubuntu/backup/<file> success". The `otacheck_state.txt` missing log is a known normal non-fatal pattern and does NOT indicate a failure.

**When active:** Always — recovery thread starts at every boot
**Frequency:** Init at boot; sanity loop every 900s (~15 min)
**Cross-service impact:** Ensures config files are intact for bagheera, cloudconfig, and locale config services

**Key log patterns (normal boot — checksums match, no recovery needed):**
```
RCVRY: I: <pid>: <pid>: Init recovery
RCVRY: I: <pid>: <pid>: Creating object: /home/ubuntu/.nddevice/nddevice.ini
RCVRY: I: <pid>: <pid>: Creating object: /home/ubuntu/config/deviceconfig.ini
RCVRY: I: <pid>: <pid>: Creating object: /home/ubuntu/.nddevice/latest/cloudconfig.ini
RCVRY: I: <pid>: <pid>: Found locale configuration: CA,MX,US
RCVRY: I: <pid>: <pid>: Added locale config file: /home/ubuntu/.nddevice/latest/nd_config_CA.ini : 1
RCVRY: I: <pid>: <pid>: Added locale config file: /home/ubuntu/.nddevice/latest/nd_config_MX.ini : 1
RCVRY: I: <pid>: <pid>: Added locale config file: /home/ubuntu/.nddevice/latest/nd_config_US.ini : 1
RCVRY: I: <pid>: <pid>: Successfully processed 3 locale configuration files
RCVRY: I: <pid>: <pid>: No backup required. checksums match /home/ubuntu/.nddevice/nddevice.ini, /home/ubuntu/backup/nddevice.ini
RCVRY: I: <pid>: <pid>: /home/ubuntu/.nddevice/nddevice.ini skipping backup
RCVRY: I: <pid>: <pid>: No md5-suffixed backup present in latest for /home/ubuntu/.nddevice/latest/bagheera_config.ini, no backup needed
RCVRY: I: <pid>: <pid>: Backup result: 1
RCVRY: I: <pid>: <pid>: Creating thread
SVC: I: <pid>: <pid>: Recovery init status: 1
RCVRY: I: <pid>: <tid>: Md5sum check passed for /home/ubuntu/.nddevice/latest/bagheera_config.ini against backup /home/ubuntu/backup/md5/bagheera_config.ini.<hash>
RCVRY: I: <pid>: <tid>: Sanity passed
```

**Key log patterns (recovery triggered):**
```
RCVRY: I: <pid>: <tid>: File not present: /home/ubuntu/config/deviceconfig.ini
RCVRY: I: <pid>: <tid>: File size Zero: /home/ubuntu/.nddevice/nddevice.ini
RCVRY: I: <pid>: <tid>: Starting primary recovery for file: /home/ubuntu/backup/deviceconfig.ini
RCVRY: I: <pid>: <tid>: Primary recovery for file: /home/ubuntu/backup/deviceconfig.ini success
RCVRY: I: <pid>: <tid>: recovery status: 1
```

**Normal non-fatal pattern (NOT a failure):**
```
RCVRY: I: <pid>: <pid>: File not present: /dev/shm/nd_files_c/otacheck_state.txt
```
This appears every boot because otacheck_state.txt lives in tmpfs and is not created until otacheck runs.

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_svc_289` | `tests/svc/test_tc_svc_289_config_file_checksum_match.py` | "Init recovery" logged; checksums match on normal boot | — |
| `TC_svc_293` | `tests/svc/test_tc_svc_293_recovery_sanity_check.py` | "Sanity passed" logged; interval between sanity checks 850000–950000ms | `DT-3950` |
| `TC_svc_855` | `tests/svc/test_tc_svc_855_config_recovery_deviceconfig_missing.py` | deviceconfig.ini deleted → "File not present" + recovery success + file size non-zero + md5 matches backup | `DT-3543` |
| `TC_svc_857` | `tests/svc/test_tc_svc_857_config_recovery_deviceconfig_size_zero.py` | deviceconfig.ini zeroed → "File size Zero" + recovery success + file size non-zero + md5 matches backup | — |
| `TC_svc_1167` | `tests/svc/test_tc_svc_1167_config_recovery_nddevice_missing.py` | nddevice.ini deleted → recovery success | — |
| `TC_svc_1169` | `tests/svc/test_tc_svc_1169_config_recovery_nddevice_size_zero.py` | nddevice.ini zeroed → recovery success | — |
| `TC_svc_1230` | `tests/svc/test_tc_svc_1230_config_recovery_bagheera_config_missing.py` | bagheera_config.ini deleted → recovery success | `DT-3780`, `DT-3543` |
| `TC_svc_1232` | `tests/svc/test_tc_svc_1232_config_recovery_bagheera_config_size_zero.py` | bagheera_config.ini zeroed → md5 check triggered → recovery success | `DT-3780` |

---

### Flow 9: Config Backup on Boot

**What happens:** During recovery init, SVC computes md5sums of monitored config files and compares them against backup copies in `/home/ubuntu/backup/`. If the backup is missing or the md5 has changed, SVC writes a new backup and logs "backup status: 1". If checksums match, SVC logs "No backup required. checksums match <live>, <backup>" and skips the write. Files with no md5-suffixed backup in `/home/ubuntu/.nddevice/latest/` (e.g., bagheera_config.ini, bagheera_config.spec, bagheera_override.ini) log "No md5-suffixed backup present in latest for <file>, no backup needed" and are skipped. The overall result is logged as "Backup result: 1" on success.

**When active:** Always at every boot as part of recovery init
**Frequency:** Once at boot
**Cross-service impact:** Provides restore source for Flow 8 recovery

**Key log patterns:**
```
RCVRY: I: <pid>: <pid>: No backup required. checksums match /home/ubuntu/.nddevice/nddevice.ini, /home/ubuntu/backup/nddevice.ini
RCVRY: I: <pid>: <pid>: /home/ubuntu/.nddevice/nddevice.ini skipping backup
RCVRY: I: <pid>: <pid>: No md5-suffixed backup present in latest for /home/ubuntu/.nddevice/latest/bagheera_config.ini, no backup needed
RCVRY: I: <pid>: <pid>: /home/ubuntu/.nddevice/latest/bagheera_config.ini skipping backup
RCVRY: I: <pid>: <pid>: Backup result: 1
```

When backup IS taken (file changed since last boot):
```
RCVRY: I: <pid>: <pid>: /home/ubuntu/config/deviceconfig.ini needs update, taking backup
RCVRY: I: <pid>: <pid>: backup status: 1
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_svc_1234` | `tests/svc/test_tc_svc_1234_config_backup_deviceconfig.py` | deviceconfig.ini backup exists at `/home/ubuntu/backup/deviceconfig.ini`; md5 matches live file | — |
| `TC_svc_1235` | `tests/svc/test_tc_svc_1235_config_backup_nddevice.py` | nddevice.ini backup exists; md5 matches live file | — |
| `TC_svc_1236` | `tests/svc/test_tc_svc_1236_config_backup_bagheera_config.py` | bagheera_config.ini backup exists or "no backup needed" logged; md5 matches | `DT-3780` |
| `TC_svc_1237` | `tests/svc/test_tc_svc_1237_config_backup_cloudconfig.py` | cloudconfig.ini backup exists; md5 matches live file | — |
| `TC_svc_1238` | `tests/svc/test_tc_svc_1238_config_backup_nd_config.py` | nd_config_recovery.ini backup exists; md5 matches live file | — |

---

### Flow 10: Disk Monitoring

**What happens:** SVC's disk monitor thread starts at boot and polls disk space every 180 seconds (`diskmon_poll`). On the first poll it runs a `du -Sh` command to find the largest directories, then runs `find / -type f ... | sort -rh | head -20` to find the largest individual files. Subsequent polls check free space on the relevant partitions (bagheera: `/` and `/media/data`; krait: `/` and `/data`) and log "Free space : <bytes>". If free space on any partition falls below the configured threshold, SVC triggers cleanup. On krait/krait2, cleanup targets the `/data` partition. On bagheera devices, cleanup targets the root partition.

**When active:** Always — diskmon starts at every boot when `Diskmon enabled: 1`
**Frequency:** Every 180s
**Cross-service impact:** Cleanup may delete log files and data files from other services

**Key log patterns (startup):**
```
DISK: I: <pid>: <pid>: Init diskmon
SVC: I: <pid>: <pid>: Diskmon init status: 1
DISK: I: <pid>: <tid>: Executing cmd: du -Sh --exclude /media/data/nd_sdcard/ / 2> /dev/null | sort -rh 2> /dev/null | head -20
DISK: I: <pid>: <tid>: Executing cmd: find / -type f -not -path "/media/data/nd_sdcard/*" -exec du -Sh {} + 2> /dev/null | sort -rh 2> /dev/null | head -20
```

Periodic free space log (every ~180s):
```
DISK: I: <pid>: <tid>: Free space : 3816157184
DISK: I: <pid>: <tid>: Filesystem state:         clean
```

Cleanup triggered (when below threshold):
```
DISK: I: <pid>: <tid>: Triggering cleanup
DISK: I: <pid>: <tid>: CleanUP() freed <N> bytes
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_svc_299` | `tests/svc/test_tc_svc_299_diskmon_init.py` | "Init diskmon" logged; "Diskmon init status: 1" logged; du/find command execution logged | `DT-3926` |
| `TC_svc_328` | `tests/svc/test_tc_svc_328_disk_monitoring_free_space_check.py` | "Free space" logged approximately every 180s | `DT-3926` |
| `TC_svc_329` | `tests/svc/test_tc_svc_329_free_space_less_threshold_cleanup_krait.py` | Cleanup triggered on `/data` partition when below threshold (krait/krait2 only) | `DT-3802` |
| `TC_svc_350` | `tests/svc/test_tc_svc_350_free_space_cleanup_root.py` | Cleanup triggered on `/` root partition when below threshold | — |

---

### Flow 11: Button Press Detection

**What happens:** SVC monitors a GPIO input for physical button press events. A short press (falling edge) sends a user-alert message to ndcentral with button index 0: "Sending message for button falling: 0". A long press (≥ `long_press_duration_ms`, default 5000ms) while ignition is ON triggers installer scan: "Sending message for button longpress for installer: 0". A long press while ignition is OFF triggers privacy mode: "Sending message for button longpress: 0". All messages are forwarded to ndcentral via MSGQ.

**When active:** Always — SVC monitors GPIO at all times
**Frequency:** Per button press event
**Cross-service impact:** ndcentral (bagheera) must receive and process the message; INSTALLER_SCAN_INDICATE triggers OTA scan

**Key log patterns:**
```
SVC: I: <pid>: <tid>: Sending message for button falling: 0
SVC: I: <pid>: <tid>: Sending message for button longpress for installer: 0
SVC: I: <pid>: <tid>: Sending message for button longpress: 0
SVC: I: <pid>: <tid>: Sending msg to ndcentral
```

Expected in ndcentral (bagheera) logs:
```
User alert msg received, button: 0
INSTALLER_SCAN_INDICATE msg received
BUTTON_LONG_PRESS received for button 0
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_svc_330` | `tests/svc/test_tc_svc_330_detect_button_press.py` | "Sending message for button falling: 0" in svc logs; "User alert msg received, button: 0" in ndcentral logs | — |
| `TC_svc_331` | `tests/svc/test_tc_svc_331_long_press_installer.py` | "Sending message for button longpress for installer: 0" in svc logs; "INSTALLER_SCAN_INDICATE msg received" in ndcentral logs | — |
| `TC_svc_332` | `tests/svc/test_tc_svc_332_ignitionoff_longpress.py` | "Sending message for button longpress: 0" in svc logs; "BUTTON_LONG_PRESS received for button 0" in ndcentral logs | — |

---

### Flow 12: MSP/QCS Alive Signal (krait/krait2 only)

**What happens:** On krait and krait2 devices, SVC communicates with the MSP (Microcontroller) to set the QCS (Qualcomm CPU Subsystem) alive configuration. SVC reads the required alive config value from bagheera config, checks the current MSP configuration, and sends the alive signal if needed. On success: "MSP setup successfull" and "MSP QCS alive signal sent successfully". The device must remain stable for at least 120 seconds after the signal is sent to confirm no regression.

**When active:** krait / krait2 only
**Frequency:** Once per boot
**Cross-service impact:** MSP must acknowledge the alive signal; affects device stability on krait platforms

**Key log patterns:**
```
SVC: I: <pid>: <tid>: Entered to check and send MSP qcs alive with retry_count 1
SVC: I: <pid>: <tid>: required_msp_qcs_alive_config_value Alive enabled in bagheera config is 3
SVC: I: <pid>: <tid>: Current QCS Alive configuration in MSP is 3
SVC: I: <pid>: <tid>: MSP setup successfull
SVC: I: <pid>: <tid>: MSP QCS alive signal sent successfully
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_svc_327` | `tests/svc/test_tc_svc_327_msp_qcs_alive_signal.py` | All 5 MSP log patterns present; device stable for 120s after signal (krait/krait2 only) | — |

---

### Flow 13: Low-Power Wakeup (LPW) Behavior

**What happens:** When the device enters LPW (ignition off → device powers down → wakes up on timer), SVC's keepalive monitoring interacts with the power_monitor and bagheera LPW flows. TC_2792 validates normal LPW reboot on ignition off: APM detects IGN OFF, power_monitor tracks crank voltage state change to 0, and SVC logs keepalive timeout for bagheera after the configured shutdown duration. TC_2797 validates that bagheera keepalive monitoring is suppressed during LPW when bagheera is inactive (lowpower_wakeups≥1 in power_mon logs; device does NOT reboot when bagheera is killed). TC_2798 validates that killing power_monitor during LPW causes SVC to trigger a reboot via keepalive timeout ("Keep alive timeout: power_monitor").

**When active:** When device enters/exits LPW cycle
**Frequency:** Per ignition off/on event
**Cross-service impact:** apm (IGN OFF detection), power_monitor (crank voltage, wakeup counter), bagheera (inactive during LPW)

**Key log patterns (TC_2792 — ignition off reboot):**
```
(apm logs) IGN: OFF
(power_mon logs) crank voltage state changed 0
SVC: E: <pid>: <pid>: Keep alive timeout:  bagheera diff: 120 Restarting Service
(power_mon logs) misc_lowpower_wakeup: 0
(power_mon logs) lowpower_wakeups 0
```

**Key log patterns (TC_2797 — LPW bagheera inactive):**
```
(power_mon logs) lowpower_wakeups 1
```
Device does NOT reboot when bagheera killed during LPW (keepalive suppressed).

**Key log patterns (TC_2798 — LPW powermon crash):**
```
(svc logs) Keep alive timeout:  power_monitor diff: 300 Restarting Service
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_svc_2792` | `tests/svc/test_tc_svc_2792_reboot_ignition_off.py` | IGN OFF in apm logs; crank state change in power_mon; keepalive timeout bagheera in svc; misc_lowpower_wakeup: 0; reboot timing 660–720s (skips bagheera2) | `DT-4247`, `DT-3912` |
| `TC_svc_2797` | `tests/svc/test_tc_svc_2797_lpw_bagheera_inactive.py` | lowpower_wakeups≥1 in power_mon; device does NOT reboot after bagheera killed during LPW | `DT-3912` |
| `TC_svc_2798` | `tests/svc/test_tc_svc_2798_lpw_powermon_crash.py` | "Keep alive timeout: power_monitor" in svc logs after power_monitor killed during LPW (krait/krait2 only) | — |

---

### Flow 14: Locale Config File Critical Events

**What happens:** During recovery init, SVC attempts to add each locale config file (nd_config_CA.ini, nd_config_MX.ini, nd_config_US.ini, etc.) to its recovery object list. If a locale file is present and readable, it logs "Added locale config file: <path> : 1". If the file is missing (deleted before reboot), it logs "Added locale config file: <path> : 0" — this is the critical event signal indicating the file was absent at startup. TC_2799 validates this by deleting locale files before reboot and confirming the ": 0" status is logged.

**When active:** Always at every boot — locale config files are always checked
**Frequency:** Once at boot during recovery init
**Cross-service impact:** Missing locale configs affect nd_config-dependent features (locale-specific camera/GPS config)

**Key log patterns (files present — normal):**
```
RCVRY: I: <pid>: <pid>: Added locale config file: /home/ubuntu/.nddevice/latest/nd_config_CA.ini : 1
RCVRY: I: <pid>: <pid>: Added locale config file: /home/ubuntu/.nddevice/latest/nd_config_MX.ini : 1
RCVRY: I: <pid>: <pid>: Added locale config file: /home/ubuntu/.nddevice/latest/nd_config_US.ini : 1
RCVRY: I: <pid>: <pid>: Successfully processed 3 locale configuration files
```

**Key log patterns (file deleted — critical event):**
```
RCVRY: I: <pid>: <pid>: Added locale config file: /home/ubuntu/.nddevice/latest/nd_config_MX.ini : 0
RCVRY: I: <pid>: <pid>: Added locale config file: /home/ubuntu/.nddevice/latest/nd_config_US.ini : 0
RCVRY: I: <pid>: <pid>: Added locale config file: /home/ubuntu/.nddevice/latest/nd_config_UK.ini : 0
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_svc_2799` | `tests/svc/test_tc_svc_2799_config_file_deleted_critical_event.py` | Delete nd_config_MX.ini + nd_config_US.ini → reboot → "Added locale config file: .../nd_config_MX.ini : 0" and "...nd_config_US.ini : 0" in svc logs; then delete nd_config_UK.ini → reboot → "...nd_config_UK.ini : 0" | — |

---

## Config-Driven Flow Activation

The agent MUST read the device config from `device_data/device_<ID>_config.ini` before selecting test cases. Use the following mapping:

| Config Section | Config Key | Value | Activates Flow(s) | Test Cases Affected |
|---|---|---|---|---|
| `[svc]` | `recovery_enabled` | `1` (default) | Flow 8, 9 | TC_svc_289, TC_svc_293, TC_svc_855, TC_svc_857, TC_svc_1167, TC_svc_1169, TC_svc_1230, TC_svc_1232, TC_svc_1234–1238 |
| `[svc]` | `diskmon_enabled` | `1` (default) | Flow 10 | TC_svc_299, TC_svc_328, TC_svc_329, TC_svc_350 |
| `[svc]` | `config_recovery_poll` | `900` (default) | Flow 8 sanity interval | TC_svc_293 |
| `[svc]` | `diskmon_poll` | `180` (default) | Flow 10 poll interval | TC_svc_328 |
| `[svc]` | `svc_failure_case_timeout` | `300` (default) | Flow 6 keepalive timeout | TC_svc_295, TC_svc_302, TC_svc_303 |
| `[watchdog]` | `watchdog_timeout` | `90` (default) | Flow 3 watchdog timer | TC_svc_291, TC_svc_292, TC_svc_363 |
| device_type | `bagheera3` / `octo` | — | Flow 3 AON WDT (600s) | TC_svc_291 (AON step) |
| device_type | `krait` / `krait2` | — | Flow 12 MSP/QCS | TC_svc_327 |
| device_type | `krait` / `krait2` | — | Flow 13 TC_2798 | TC_svc_2798 |
| — | — | — | Flows 1, 2, 4, 5, 6, 7, 11, 14 (always active) | all other TCs |

**Rules:**
- Flows marked "always active" → run their test cases unconditionally on all device types
- `recovery_enabled=0` or `diskmon_enabled=0` → skip respective flows
- Device-type-restricted TCs (TC_287, TC_303, TC_327, TC_329, TC_353, TC_2798) → skip if device_type does not match
- TC_2792 skips bagheera2 explicitly
- If config key is missing → use the default value listed above

---

## Cross-Service Dependencies

| Related Service | Why | When to check its logs |
|---|---|---|
| `bagheera` (nd_central) | Sends keepalive to Q_SVC; receives button press messages from SVC | Flow 5 (KA messages), Flow 6 (KA timeout), Flow 7 (post-restart), Flow 11 (button press) |
| `power_monitor` | Sends keepalive to Q_SVC; reports crank voltage + LPW wakeup count | Flow 5 (KA messages), Flow 6 (KA timeout), Flow 13 (LPW) |
| `apm` | Sends keepalive to Q_SVC; detects IGN OFF event | Flow 5 (KA messages), Flow 6 (KA timeout), Flow 13 (LPW — IGN OFF detection) |
| `awsiot` | Sends keepalive to Q_SVC (monitoring suppressed at startup); timeout 120s | Flow 4 (registration), Flow 6 (KA timeout) |
| `analyticsService` | Sends keepalive to Q_SVC; timeout 180s | Flow 4 (registration), Flow 6 (KA timeout) |
| `otacheck` | State file `/dev/shm/nd_files_c/otacheck_state.txt` is monitored by RCVRY; always missing at early boot (tmpfs) | Flow 8 — RCVRY "File not present: otacheck_state.txt" is normal non-fatal |

---

## Flow Dependency Graph

```
boot
 └─► [Flow 1: Init & Config Parsing]
      ├─► [Flow 2: MSGQ Q_SVC creation]
      ├─► [Flow 3: Watchdog Init (AON+PMIC)] ──► periodic PMIC kicks every ~30s
      ├─► [Flow 9: Config Backup on Boot]
      └─► [Flow 8: Recovery Thread Init]
               └─► [Flow 8: Sanity loop every 900s] ──► on file issue: recovery
               └─► [Flow 14: Locale config file status (: 1 or : 0)]
      └─► [Flow 4: KA Registration] ──► [Flow 5: KA Messages (normal)]
                                   └─► [Flow 6: KA Timeout → reboot] (on miss)
                                   └─► [Flow 7: KA Reset after service restart]
      └─► [Flow 10: Diskmon thread every 180s] ──► cleanup if below threshold
      └─► [Flow 11: Button press GPIO monitoring]
      └─► [Flow 12: MSP/QCS alive signal] (krait/krait2 only)

ignition off event
 └─► [Flow 13: LPW — apm IGN OFF → power_mon crank 0 → SVC KA timeout bagheera]
```

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Determine active flows** using the Config-Driven Flow Activation table above
3. **Check device type** to apply device-type-restricted test case skips
4. **For each active flow**, read the mapped Python test files from `tests/svc/`
5. **From each test file**, use assertion patterns and the `log_paths` dict for device-type-specific log directories
6. **Search device logs** in `device_logs/<device_id>/svc.log` using patterns from this skill and the test files
7. **For cross-service checks** (button press, LPW, keepalive timeout), also search `bagheera.log`, `power_monitor.log`, `apm.log`
8. **Treat "File not present: /dev/shm/nd_files_c/otacheck_state.txt" as non-fatal** — it always appears at boot
9. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED
