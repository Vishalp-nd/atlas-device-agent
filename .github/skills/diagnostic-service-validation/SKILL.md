---
name: diagnostic-service-validation
description: "Use when: validating Diagnostic (diagnostic) service behavior from device logs. Covers service initialization, MSGQ creation, EMMC/SDCard health monitoring (wear level, bad blocks, write data), memory usage tracking, fan speed logging, free space (RAM) monitoring, SD card/EMMC detection, process info and CPU/GPU info threads, DB management and recovery, fsck filesystem checks, overlay management, health analytics publishing, power/APM/WOM metrics forwarding, WAF monitoring, and SD card unmount/remount recovery."
argument-hint: "device serial (e.g., /diagnostic-service-validation 103202400271)"
---

# Diagnostic Service (`diagnostic`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the Diagnostic service —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads test cases for actual log patterns,
> device-type paths, and acceptance criteria — this skill does NOT duplicate those.

---

## Service Overview

`diagnostic` is a critical always-running service that monitors hardware health and reports status to cloud via health analytics and HealthStats. It handles EMMC and SD card health monitoring (wear level, bad blocks, write data, remaining life), memory usage tracking (used memory per block device), fan speed logging, free space (RAM) monitoring, SD card/EMMC detection and manufacturer info collection, process info and CPU/GPU info threads, fsck filesystem error checks and recovery, overlay filesystem management, and forwarding power/APM/WOM metrics from other services. The service interacts with circular_buffer, power_monitor, ext_cam, conn_mgr, wifi_mgr, and gps via message queues.

**Process name:** `diagnostic`
**Binary location:** `/home/ubuntu/.nddevice/latest/service/diagnostic/diagnostic`
**Log file:** `diagnostic.log` (path: `/home/ubuntu/.nddevice/log/diagnostic/`)
**Primary config sections:** `[diagnostic]`, `[healthstats]`
**Config files:** `bagheera_config.ini`, `bagheera_override.ini`
**Message queue name:** `DIAGNOSTIC`
**DB path:** `/home/ubuntu/.nddevice/db/diagnostic.db`
**Health files:**
- EMMC: `/home/ubuntu/.nddevice/log/diagnostic/health_emmc.txt`
- SDCard: `/home/ubuntu/.nddevice/log/diagnostic/health_sdcard.txt`

---

## Device-Type-Specific Block Devices

| Device Type | EMMC Block | SD Card Block | EMMC Mount Point | SD Card Mount | CLE Tool Path |
|---|---|---|---|---|
| krait/krait2 | `/dev/mmcblk0p66` | N/A (uses /data) | `/` | `/data` | N/A |
| bagheera2 | `/dev/mmcblk0` | `/dev/mmcblk2` | `/` | `/media/data` | `/home/ubuntu/.nddevice/CLE_tool/` |
| bagheera3 | `/dev/mmcblk0` | `/dev/mmcblk1` | `/` | `/media/data` | `/home/ubuntu/.nddevice/CLE_tool/` |
| octo | N/A | `/dev/nvme0n1p17` | N/A | NVMe storage | N/A |

---

## Service Flows

### Flow 1: Service Initialization & Configuration Parsing

**What happens:** On startup, the service initializes the logger, creates NDService object (`DIAG` tag), opens/creates the diagnostic SQLite DB (`diagnostic.db`), creates the message queue (`DIAGNOSTIC`), parses `bagheera_config.ini` for config values (sdcard_diag_interval_time, cpugpuinfo_time, processinfo_time, waf_diag_interval_time), waits for time_sync token file (up to 30s), reads UDID from property DB, spawns SdCard thread, ProcessInfo thread, CpuGpuInfo thread, generic_test thread, and a periodic health metrics timer (60s). Override configs from `bagheera_override.ini` are applied.

**When active:** Always (on every service start/restart)
**Frequency:** Once at boot / on service restart
**Cross-service impact:** service_mon monitors this service; circular_buffer and power_monitor send metrics to diagnostic MSGQ

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `############### starting diagnostic ################` | Service startup marker |
| `Message queue created` | MSGQ successfully created |
| `success in open DB` | SQLite DB opened |
| `success mutex init` | DB mutex initialized |
| `success in create_table_db` | DB table created/verified |
| `sdcard_diag_interval_time: <N>` | SD card check interval configured |
| `cpugpuinfo_time: <N>` | CPU/GPU info thread interval |
| `processinfo_time: <N>` | Process info thread interval |
| `waf_diag_interval_time: <N>` | WAF monitoring interval |
| `Execute generic device test start` | Generic monitoring thread started |
| `OVerride file parsed successfully` | Override config applied |
| `Override file <path> present` | Override file detected |
| `udid: <N>` | UDID loaded from property DB |
| `udid updated by time_sync service after waiting here for <N> sec.` | Time sync complete |
| `CTOR called for CPUGPUINFO` | CPU/GPU info thread created |
| `execute_thread(), component: SDCARD` | SD card thread started |
| `unable to init logger :: Exiting from main` | Fatal: logger init failure |
| `DIAG Failed to open DB` | Critical: DB open failure |

**Test cases that validate this flow:**
| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_diagnostic_1487` | Service is active/running | `DT-3865`, `DT-3173`, `BG4-528` |
| `TC_diagnostic_1488` | MSGQ creation successful | — |
| `TC_diagnostic_1579` | Override config parsed | `BG4-1030`, `DT-3629`, `DT-3073` |
| `TC_diagnostic_1589` | Config push parsed correctly | — |
| `TC_diagnostic_1636` | Logger initialization check | `BG4-929`, `BG4-634` |

---

### Flow 2: Memory Usage Monitoring

**What happens:** A generic_test thread runs every 60 seconds and calls `get_system_memory_info()` to check used memory percentage for internal eMMC and external SD card (by device node), and free RAM space in MB. Results are logged with "used memory of <dev_node> is <percent>" and "Free space in RAM is <MB>".

**When active:** Always
**Frequency:** Every 60 seconds
**Cross-service impact:** None — informational logging; data also used in storage metrics publishing

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `used memory of /dev/mmcblk0 is <percent>` | Internal EMMC usage (bagheera) |
| `used memory of /dev/mmcblk1 is <percent>` | External SD card usage (bagheera3) |
| `used memory of /dev/mmcblk2 is <percent>` | External SD card usage (bagheera2) |
| `Free space in RAM is <MB>` | Free RAM in megabytes |
| `Failed to get system memory info` | Memory check failed |
| `internal_total <N>` | Internal storage total MB |
| `internal_available <N>` | Internal storage available MB |
| `external_total <N>` | External storage total MB |
| `external_available <N>` | External storage available MB |

**Test cases that validate this flow:**
| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_diagnostic_1194` | Memory check command executes | `BG4-913` |
| `TC_diagnostic_1596` | Used memory logged | — |
| `TC_diagnostic_1616` | Free space in RAM logged | — |
| `TC_diagnostic_1623` | Internal/external memory block info | — |

---

### Flow 3: SD Card Health Check

**What happens:** The SdCard component thread runs at configured `sdcard_diag_interval_time` (default 30-60s). It detects the SD card manufacturer (sandisk, toshiba, micron, kingston) and runs manufacturer-specific OEM tools (CLE_tool) to get health stats: Host Write Count, Average Erase Count (MLC/SLC/Global), Bad Block counts, and Spare Blocks. Results are written to `health_sdcard.txt`. Copy/delete time averages are also measured.

**When active:** Always on bagheera2/bagheera3 (devices with external SD card)
**Frequency:** Every `sdcard_diag_interval_time` seconds (configurable, default 30-60)
**Cross-service impact:** Health data sent to circular_buffer for cloud reporting

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `diagnosis entered for SDcard` | SD card health check cycle started |
| `Executing SD card health check for <manufacturer>` | SD health check running |
| `final_cmd: <cmd>` | OEM tool command being executed |
| `KSI : Host Write Count : <N>` | Kingston: Host Write Count |
| `KSI : Avg Erase Count MLC : <N>` | Kingston: Average Erase Count |
| `KSI : Total No. of Later Bad Blocks : <N>` | Kingston: Bad blocks |
| `KSI : Total No. of Spare Blocks : <N>` | Kingston: Spare blocks |
| `SDcard detected` | SD card found |
| `Failed to detect sdcard` | SD card not found |
| `sdcard is connected` | SD card present (bagheera) |
| `Not running oem tool for emmc, It has run successfully` | OEM tool already ran |

**Test cases that validate this flow:**
| Test Case ID | Pytest Path | What it checks | Related Bugs |
|---|---| --- |
| `TC_diagnostic_1597` | SD card detected | — |
| `TC_diagnostic_1610` | SD card health check runs | — |
| `TC_diagnostic_1644` | SD card health check negative | — |

**Health file validation patterns (health_sdcard.txt):**
| Card Type | Grep Pattern | Validate |
|---|---|
| Kingston (manfid 0x000070) | `Host Write Count` | >= 1 |
| Kingston | `Avg Erase Count MLC` | >= 1 |
| Kingston | `Total No. of Later Bad Blocks` | exists |
| Kingston | `Total No. of Spare Blocks` | >= 1 |
| Sandisk/bagheera2 | `Avg Erase Count POOL3` | >= 1 |
| Sandisk/bagheera2 | `Bad Block Runtime POOL3` | exists |
| Sandisk/bagheera2 | `Cumulative Write Data Size In 100MB` | >= 1 |

**Health file validation patterns (health_emmc.txt) — krait devices:**
| Grep Pattern | Validate |
|---|---|
| `Avg Erase Count POOL3` | >= 1 |
| `Bad Block Runtime POOL3` | exists |
| `Cumulative Write Data Size In 100MB` | >= 1 |

**Manfid detection (bagheera3):**
```bash
cat /sys/class/mmc_host/mmc1/mmc1:0001/manfid
# 0x000070 = Kingston → MLC params path
# Other = Sandisk → POOL3 params path
```

---

### Flow 4: EMMC Health Check

**What happens:** EMMC health check runs alongside SD card diagnosis. The service detects the EMMC manufacturer (sandisk, toshiba, micron) and runs CLE_tool to extract: Cumulative Write Data Size (in 100MB units), Average Erase Count MLC, and Bad Block Runtime MLC. Results use CMD62/CMD63 MMC commands. Health data is logged and written to health files.

**When active:** Always on bagheera2/bagheera3
**Frequency:** Every `sdcard_diag_interval_time` seconds
**Cross-service impact:** Health data included in storage metrics published to cloud

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `Executing EMMC health check for <manufacturer>` | EMMC check running |
| `CMD62 and CMD63 read successfully from /dev/mmcblk0` | MMC commands succeeded |
| `WD : Cumulative Write Data Size In 100MB : <N>` | EMMC write volume |
| `WD : Avg Erase Count MLC : <N>` | EMMC avg erase count |
| `WD : Bad Block Runtime MLC : <N>` | EMMC bad blocks |
| `emmc detected` | EMMC found |
| `Internal Emmc is mounted` | Internal EMMC mount confirmed |
| `Internal eMMC is MOUNTED at: <path>` | EMMC mount point |

**Test cases that validate this flow:**
| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_diagnostic_1489` | EMMC health file exists | `BG4-528` |
| `TC_diagnostic_1599` | EMMC detected | — |
| `TC_diagnostic_1611` | EMMC health check runs | — |
| `TC_diagnostic_1645` | EMMC health check negative | — |

---

### Flow 5: Manufacturer Info Collection

**What happens:** The service collects storage manufacturer info by reading sysfs files for both internal (EMMC) and external (SD card) storage: manfid (manufacturer ID), name, oemid (OEM ID), and serial number. Data is read from `/sys/class/mmc_host/mmcN/mmcN:NNNN/` paths specific to each device type and included in storage metrics JSON.

**When active:** Always on bagheera2/bagheera3 (when storage metrics are collected)
**Frequency:** On each storage metrics publish cycle (every 5 minutes)
**Cross-service impact:** Data published to cloud analytics

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `Collecting eMMC manufacturer information...` | Manufacturer info collection started |
| `Internal manfid: <value>` | Internal EMMC manufacturer ID |
| `External manfid: <value>` | External SD manufacturer ID |
| `Internal name: <value>` | Internal EMMC name |
| `External name: <value>` | External SD name |
| `Internal OEMID: <value>` | Internal OEM ID |
| `External OEMID: <value>` | External OEM ID |
| `Internal serial: <value>` | Internal serial number |
| `External serial: <value>` | External serial number |

**Test cases that validate this flow:**
| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_diagnostic_1635` | Manufacturer info collected | `BG4-631` |

---

### Flow 6: Fan Speed Logging

**What happens:** In the generic_test thread, fan speed is checked every 60 iterations (approximately once per hour, since the loop sleeps 60s). Calls `nd_device_obj->get_fan_status()` which returns RPM value. Stored in `power_health_info.fan_spd` and included in power metrics JSON. If check fails, logs error.

**When active:** Always (on devices with fan hardware)
**Frequency:** Once per hour (counter resets every 60 iterations of 60s loop)
**Cross-service impact:** Fan speed included in power metrics sent to cloud

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `FAN speed : <N>` | Fan speed in RPM |
| `Failed to check FAN speed` | Fan speed read failed |

**Test cases that validate this flow:**
| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_diagnostic_1580` | Fan speed logged | — |
| `TC_diagnostic_1648` | Fan speed negative scenario | — |

---

### Flow 7: ProcessInfo & CPU/GPU Info Threads

**What happens:** Two monitoring threads run at configurable intervals (default 5s each):
- **ProcessInfo thread**: Monitors top memory/CPU consuming processes (top 20 by memory, top 30 by CPU), reads `/proc/meminfo`, tracks hardware memory, sends data to HealthStats.
- **CpuGpuInfo thread**: Monitors GPU usage via DMA buffer, CPU load, GPU temperature, polls at `gpu_poll_secs` interval. Reports metrics to cloud.

**When active:** Always
**Frequency:** ProcessInfo: every `process_info_secs` (default 5s); CpuGpuInfo: every `cpu_gpu_info_secs` (default 5s)
**Cross-service impact:** Metrics published to HealthStats cloud endpoint

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `===========starting processinfo thread=================` | ProcessInfo thread started |
| `=============starting cpu gpu free info thread==============` | CpuGpuInfo thread started |
| `Starting GPU info thread with poll time: <N> seconds` | GPU poll thread started |
| `pinfo_cond.wait_for is timed out` | ProcessInfo cycle triggered |
| `cpugpuinfo_cond.wait_for is timed out` | CpuGpuInfo cycle triggered |
| `Get DMA BUF` | GPU DMA buffer read |
| `DMA Buf file opened` | GPU DMA file accessible |
| `sleeping on pinfo_cond` | ProcessInfo waiting for next cycle |
| `sleeping on cpugpuinfo_cond` | CpuGpuInfo waiting for next cycle |
| `Entered add hardware memory ....` | Hardware memory info collected |

**Test cases that validate this flow:**
| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_diagnostic_1660` | Both threads started | — |

---

### Flow 8: Health Metrics Collection & Publishing

**What happens:** A timer fires every 60 seconds (`get_health_metrics`). It sends REQ_POWERMON_METRICS to power_monitor, REQ_HEALTH_INFO to conn_mgr/wifi_mgr/gps, REQ_DHUB_INFO to ext_cam (if enabled), and REQ_STORAGE_INFO to circular_buffer (every 5th cycle = every 5 minutes). When responses arrive via MSGQ:
- **RES_POWERMON_METRICS**: Power metrics (ignition status, LPW count, voltage, temperature, shutdown reason) published to cloud analytics + HealthStats
- **RES_STORAGE_INFO**: Storage metrics (file counts, memory usage, erase counts, bad blocks, manufacturer info) published
- **RES_DHUB_INFO**: DHUB/external camera metrics published
- **RES_APM_METRICS / RES_APM_THRESHOLDS**: APM metrics forwarded to HealthStats
- **RES_WOM_METRICS**: WOM (Wake-on-Motion) metrics forwarded

**When active:** Always
**Frequency:** Health collection every 60s; storage metrics every 5 minutes
**Cross-service impact:** Requests data from power_monitor, circular_buffer, conn_mgr, wifi_mgr, gps, ext_cam

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `Initiating Health Collection For DriverI` | Health metrics timer fired |
| `Power Metrics Request Message Sent` | Request sent to power_monitor |
| `Storage Metrics Request Message Sent` | Request sent to circular_buffer |
| `DHUB Metrics Request Message Sent` | Request sent to ext_cam |
| `REQ_HEALTH_INFO request Sent` | Health info requested from services |
| `Received RES_POWERMON_METRICS` | Power metrics received |
| `Sending Power Health Metrics To HealthStats` | Power stats sent to cloud |
| `Successfully Sent Power Health Metrics To HealthStats` | Power stats confirmed |
| `Received RES_APM_METRICS` | APM metrics received |
| `Sending APM Health Metrics To HealthStats` | APM stats sent |
| `Received RES_WOM_METRICS` | WOM metrics received |
| `Received STORAGE_INFO` | Storage metrics received |
| `Sending Storage Health Metrics To HealthStats` | Storage stats sent |
| `Health analytics is disabled` | Analytics publishing skipped |
| `External Camera Feature Is Not Enabled, Skipping DHUB Metrics Request` | Ext cam disabled |

**Test cases that validate this flow:**
| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_diagnostic_1637` | Health info requests sent | — |
| `TC_diagnostic_1622` | CB message sent to diagnostic | — |
| `TC_diagnostic_1625` | EMMC/SDCard diagnosis entry | — |
| `TC_diagnostic_1646` | CB send message negative | — |

---

### Flow 9: SD Card Mount/Unmount & Fsck Recovery

**What happens:** The service handles requests from circular_buffer to mount/unmount the SD card (`REQ_CIRCBUFF_SDCARD_MOUNT`), and filesystem check requests (`REQ_CIRCULAR_BUFFER_SDCARD_FSCK_CHECK`, `REQ_CIRCBUFF_FSCK_RUN_MOUNT_FAIL`, `REQ_CIRCBUFF_FSCK_RUN_RESIZE_FAIL`). Fsck runs are forked as separate processes to check filesystem integrity and repair errors. Image correction can resize the SD card image if needed.

**Recovery Stages (from source `sdcard_recovery.h`):**
| Stage | Meaning |
|-------|---------|
| `SDCARD_RECOVERY_NOOP` | No recovery needed — card status OK |
| `SDCARD_RECOVERY_REMOVE_INSERT` | Card removal/insertion recovery attempted |
| `SDCARD_RECOVERY_ERROR` | Unrecoverable error state |

**Recovery Methods (from source `sdcard.h`):**
| Method | Meaning |
|--------|---------|
| `NATIVE_RECOVERY` | Card self-recovery |
| `FSCK` | File system check |
| `E2FSCK` | Extended filesystem check |
| `CARD_REMOVE_INSERT` | Physical card removal/insertion |
| `NO_RECOVERY` | No recovery attempted |

**Recovery Flow per device type:**
- **bagheera2**: unmount → delay 90s → circ_buff detects "findmnt | grep /media/data returned empty" → auto-enables `media-data.mount` service → SD card remounts
- **bagheera3**: unmount → reboot device → `check_sdcard_status passed.` logged → `recovery_result 1` logged → SD card remounts
- **octo**: unmount → delay 90s → circ_buff detects unmount → enables `sudo systemctl enable media-data.mount` → SD card remounts

**When active:** On request from circular_buffer
**Frequency:** On-demand
**Cross-service impact:** circular_buffer depends on SD card being mounted; fsck may cause temporary unavailability

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `received REQ_CIRCBUFF_SDCARD_MOUNT` | Mount request received |
| `umount_sdcard() Entered` | Unmount initiated |
| `mount_sdcard() Entered` | Mount initiated |
| `umount_sdcard() Returning` | Unmount completed |
| `mount_sdcard() Returning` | Mount completed |
| `creating thread to run fsck command to check for filesystem errors` | Fsck check initiated |
| `creating thread to run fsck command to check for filesystem errors and resize` | Fsck with resize |
| `findmnt \| grep /media/data returned empty. Looks like SD card got unmounted` | Unmount detected by circ_buff |
| `check_sdcard_status passed.` | SD card status check passed (bagheera3 after reboot) |
| `recovery_result 1` | Recovery succeeded (bagheera3) |
| `mount service enable cmd: systemctl enable media-data.mount` | Mount service being enabled (bagheera2) |
| `mount service enable cmd: sudo systemctl enable media-data.mount` | Mount service being enabled (octo) |
| `system_execute_with_resp cmd: systemctl enable media-data.mount` | systemctl execute (bagheera2) |
| `system_execute_with_resp cmd: sudo systemctl enable media-data.mount` | systemctl execute (octo) |
| `Done with system_execute: pclose execution return status : Success(0), command exit code:(0)` | systemctl succeeded |

**Test cases that validate this flow:**
| Test Case ID | Pytest Path | What it checks | Related Bugs |
|---|---| --- |
| `TC_diagnostic_1627` | SD card unmount/remount (positive) | — |
| `TC_diagnostic_1647` | Unmount/remount negative | — |

---

### Flow 10: Database Management & Recovery

**What happens:** On startup, the service opens/creates `diagnostic.db` SQLite database with a `COMPONENTSTATES` table tracking component states (time, system_uptime, UDID, component_state, component_substate, recovery_method, recovered, component_name). DB rows are limited. If the DB file is deleted, the service recreates it on next restart. Health files (`health_emmc.txt`, `health_sdcard.txt`) are also recreated if missing.

**When active:** Always (DB created on init)
**Frequency:** On service start; writes on each diagnosis cycle
**Cross-service impact:** None — internal state tracking

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `success in open DB` | DB opened successfully |
| `success mutex init` | DB mutex ready |
| `success in create_table_db` | DB table created |
| `DIAG Failed to open DB` | Critical: DB open failure |
| `PM Failed to create_table_db` | Critical: table creation failure |
| `db_handle_mutex init failed` | Critical: mutex failure |

**Test cases that validate this flow:**
| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_diagnostic_1651` | DB recreated after deletion | — |
| `TC_diagnostic_1652` | EMMC health file recovery | — |
| `TC_diagnostic_1653` | SDCard health file recovery | — |

---

### Flow 11: Overlay Filesystem Management

**What happens:** On D4X0 platforms (bagheera3) with external eMMC support, the service manages overlay filesystem services (var-log.mount, home-ubuntu-.nddevice-log.mount, var-backups.mount, var-tmp.mount, and corresponding sync-early services). It monitors eMMC state and handles scenarios:
- **Scenario 1**: External eMMC up but overlays failing → sync upper to internal
- **Scenario 2**: External eMMC down → force disable overlays, sync internal to external on recovery
A periodic thread checks every 5 minutes.

**When active:** Only on D4X0 platforms with external eMMC support
**Frequency:** Periodic check every 5 minutes
**Cross-service impact:** Affects log storage location; may restart overlay mount services

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `Created periodic overlay management thread (5-minute intervals)` | Overlay thread started |
| `Running overlay management wrapper` | Overlay check running |
| `Managing overlay filesystem services` | Service management active |
| `eMMC overlay services from config: <N>` | Overlay config state |
| `SCENARIO 1: External eMMC up but overlays failing` | Overlay recovery mode |
| `SCENARIO 2: External eMMC down - immediately stopping and disabling overlays` | eMMC down recovery |
| `Force disabling all overlay services` | Emergency overlay disable |
| `Forward sync successful - overlay services will be enabled` | Sync succeeded |
| `All overlay filesystem services are already in the desired state` | No action needed |
| `All overlay filesystem services configured successfully` | Configuration applied |
| `Some overlay filesystem services failed to configure` | Partial failure |
| `Critical service failure - reverting ALL overlay services` | Critical failure |

**Test cases that validate this flow:**
- Overlay management is implicitly tested in stability scenarios (TC_1604-TC_1615).

---

### Flow 12: Service Stability

**What happens:** The service must remain stable across ignition cycles (low power wakeup, ignition off), abrupt power-off events, camera crashes, AWS-triggered reboots, and cyclic reboots. The service monitors itself via service_mon. RESET_MASTERDATA messages from HealthStats reset accumulated data every 10 minutes.

**When active:** Always
**Frequency:** Continuous
**Cross-service impact:** service_mon monitors and restarts on crash

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `Receive message failed` | Critical: MSGQ receive failure |
| `Resetting master data` | Master data reset triggered by HealthStats |

**Test cases that validate this flow:**
| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_diagnostic_1604` | Stability: low power wakeup | `BG4-780`, `DT-3560` |
| `TC_diagnostic_1605` | Stability: AWS reboot | — |
| `TC_diagnostic_1606` | Stability: abrupt poweroff | `DT-3560` |
| `TC_diagnostic_1607` | Stability: camera crash | — |
| `TC_diagnostic_1614` | Stability: cyclic reboot | — |
| `TC_diagnostic_1615` | Stability: crankoff shutdown | — |
| `TC_diagnostic_1649` | LPW negative scenario | — |
| `TC_diagnostic_1655` | No network negative scenario | — |

---

### Flow 13: WAF (Write Amplification Factor) Monitoring

**What happens:** A WAF monitoring thread runs at configured interval (`waf_diag_interval_time`, default 30 minutes) to track write amplification for storage devices. This helps detect degrading storage performance.

**When active:** Always
**Frequency:** Every `waf_diag_interval_time` minutes (default 30)
**Cross-service impact:** WAF data included in storage health metrics

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `Creating thread for WAF` | WAF monitoring thread started |
| `waf_diag_interval_time: <N>` | WAF interval configured |

**Test cases that validate this flow:**
- WAF is implicitly tested within storage health check flows.

---

## Config-Driven Flow Activation

The agent MUST read the device config from `device_data/device_<ID>_config.ini`
before selecting test cases. Use the following mapping:

| Config Section | Config Key | Value | Activates Flow(s) | Test Cases Affected |
|---|---|---|---|
| `[diagnostic]` | `sdcard_diag_interval_time` | `30`/`60` (secs) | SD Card Health Check, EMMC Health Check | `TC_1597`, `TC_1599`, `TC_1610`, `TC_1611`, `TC_1644`, `TC_1645` |
| `[diagnostic]` | `sdcard_diag_start_time` | `30` (secs) | Delay before first SD card check | — |
| `[diagnostic]` | `waf_diag_interval_time` | `30` (mins) | WAF Monitoring | — |
| `[healthstats]` | `cpu_gpu_info_secs` | `5` (secs) | CPU/GPU Info thread frequency | `TC_1660` |
| `[healthstats]` | `gpu_poll_secs` | `1` (secs) | GPU polling frequency | `TC_1660` |
| `[healthstats]` | `process_info_secs` | `5` (secs) | ProcessInfo thread frequency | `TC_1660` |
| `[healthstats]` | `health_analytics` | `true`/`false` | Health analytics publishing to cloud | `TC_1637` |
| — | — | — | Init, MSGQ, Memory, Fan, DB, Stability (always active) | All non-gated TCs |

**Rules:**
- Flows marked "always active" → run their test cases unconditionally
- SD card/EMMC health checks only apply to bagheera2/bagheera3 (devices with CLE_tool)
- If config key is missing → default value is used (shown in table)
- ProcessInfo and CpuGpuInfo threads are always active regardless of config (frequency changes only)
- Overlay management only applies to D4X0 platforms (bagheera3)

---

## Cross-Service Dependencies

| Related Service | Why | When to check its logs |
|---|---|
| `service_mon` | Monitors diagnostic health; restarts on crash | When validating stability scenarios |
| `power_monitor` | Sends power metrics (RES_POWERMON_METRICS) to diagnostic | When validating health metrics/power data |
| `circular_buffer` | Sends storage metrics (RES_STORAGE_INFO); requests SD card mount/fsck | When validating storage health, mount/unmount, fsck |
| `ext_cam` | Sends DHUB metrics when external camera enabled | When validating DHUB health data |
| `time_sync` | Provides time_sync_token_file that diagnostic waits for at startup | When validating initialization timing |
| `conn_mgr` | Receives REQ_HEALTH_INFO from diagnostic | When validating health collection |
| `wifi_mgr` | Receives REQ_HEALTH_INFO from diagnostic | When validating health collection |
| `gps` | Receives REQ_HEALTH_INFO from diagnostic | When validating health collection |

---

## Flow Dependency Graph

```
boot → [Flow: Init & Config Parse] → DB setup → MSGQ creation
                                    → spawn generic_test thread → [Flow: Memory Usage] every 60s
                                                                → [Flow: Fan Speed] every hour
                                    → spawn SdCard thread → [Flow: SD Card Health] every Ns
                                                          → [Flow: EMMC Health] every Ns
                                    → spawn ProcessInfo thread → [Flow: ProcessInfo] every 5s
                                    → spawn CpuGpuInfo thread → [Flow: CPU/GPU Info] every 5s
                                    → start health timer → [Flow: Health Metrics] every 60s
                                    → spawn overlay thread → [Flow: Overlay Mgmt] every 5 min (D4X0 only)
                                    → spawn WAF thread → [Flow: WAF Monitor] every 30 min
                                    → [Flow: Msg Loop] → processes RES_POWERMON, RES_STORAGE, RES_APM, etc.
                                                       → handles REQ_CIRCBUFF_SDCARD_MOUNT, fsck requests
event (CB request) → [Flow: SD Mount/Unmount & Fsck]
event (DB deleted) → [Flow: DB Recovery] on restart
event (service crash/reboot) → [Flow: Stability] → service_mon restarts
```

## Source Code Reference
```
diagnostic/
├── inc/
│   ├── component_base.h    — Base class for diagnostic components (state machine)
│   ├── cpugpu_info.h       — CPU/GPU metrics thread
│   ├── fsck_recovery.h     — Filesystem check & recovery
│   ├── msgq.h              — Message queue handling
│   ├── process_info.h      — Process monitoring thread
│   ├── sdcard.h            — SD card health & OEM tool execution
│   ├── sdcard_recovery.h   — SD card recovery state machine
│   └── waf_utils.h         — Write Amplification Factor monitoring
└── src/
    ├── diagnostic.cpp      — Main service (init, msg loop, generic_test)
    ├── sdcard.cpp          — SD card diagnosis & health checks
    ├── sdcard_recovery.cpp — Recovery logic
    ├── cpugpuinfo.cpp      — CPU/GPU monitoring
    ├── processinfo.cpp     — Process info collection
    └── fsck_recovery.cpp   — Fsck operations
```

---

## API Reference

### ServiceController_obj
| Method | Parameters | Description |
|--------|-----------|-------------|
| `service_status` | `["diagnostic"]` | Check if diagnostic service is active |
| `restart_service` | `["diagnostic"]` | Restart the diagnostic service |

### LogAnalyzer_obj
| Method | Parameters | Description |
|--------|-----------|-------------|
| `search_logs` | `["diagnostic", "pattern"]` | Search diagnostic logs |
| `search_logs` | `["diagnostic", ["pat1","pat2"]]` | Search multiple patterns |
| `search_log_interval` | `["diagnostic", "pattern", "start_time"]` | Get interval between pattern occurrences |

### Calculator_obj
| Method | Parameters | Description |
|--------|-----------|-------------|
| `run_command_on_device` | `["cmd"]` | Execute shell command |
| `get_device_info` | `["device_type"]` | Get device type |
| `get_device_time` | none | Get current device time |
| `compare_greater_equal` | `[value, threshold]` | Compare value >= threshold |

### FileUtils_obj
| Method | Parameters | Description |
|--------|-----------|-------------|
| `file_availability` | `["path"]` | Check if file exists |
| `file_content` | `["path"]` | Read file content |

---

## Test Categories

| Category | TCs |
|----------|-----|
| Service Status & Init | TC_1487, TC_1488, TC_1489, TC_1579, TC_1589, TC_1636 |
| Memory & Free Space | TC_1194, TC_1596, TC_1605, TC_1606, TC_1607 |
| Fan Speed | TC_1625 |
| SD Card Health | TC_1597, TC_1610, TC_1611, TC_1644, TC_1645 |
| EMMC Health | TC_1599, TC_1614 |
| SD Mount/Unmount Recovery | TC_1647, TC_1652, TC_1653 |
| ProcessInfo & CpuGpuInfo | TC_1635, TC_1660 |
| Health Analytics | TC_1637 |
| DB Recovery | TC_1627 |
| Overlay Management | TC_1490 (bagheera3 only) |
| Stability | TC_1487 (implicitly via service_mon) |

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Determine device type** — affects block device paths, CLE tool availability, and SD card detection
3. **Determine active flows** using the Config-Driven Flow Activation table above
4. **For each active flow**, look up the mapped test cases for `diagnostic`
5. **Search device logs** in `device_logs/<device_id>/diagnostic.log` using patterns from this skill
6. **For cross-service checks**, also search logs of: `service_mon`, `circular_buffer`, `power_monitor`
7. **Check health files** — look for `/home/ubuntu/.nddevice/log/diagnostic/health_sdcard.txt` or `health_emmc.txt` depending on storage type
8. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED