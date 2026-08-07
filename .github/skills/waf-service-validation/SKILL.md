---
name: waf-service-validation
description: "Use when: running WAF (Write Amplification Factor) feature validation test cases on Netradyne devices. WAF is a sub-feature of the diagnostic service — it has no dedicated log file; all WAF logs appear in diagnostic.log and health.log. Covers config flow (enabled/disabled), DB file permissions and schema, internal/external eMMC data appending, backup DB schema and WAL mode, WAF value calculation (avg erase + cumulative write), DB corruption recovery (primary and backup), and stability across outward-cam crash, cyclic reboot, and no-internet conditions."
argument-hint: "device serial (e.g., /waf-service-validation 103452403525)"
---

# WAF (Write Amplification Factor) — Feature Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the WAF feature —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. WAF is NOT a standalone service; it runs as a thread inside the
> `diagnostic` service. There is no `waf.log` — all WAF log output appears in
> `diagnostic.log` (C++ side, tagged `WAF_U:`, `MMC_CMDS:`, `SDCARD:`, `DIAG:`)
> and in `health.log` (Python health service, structured JSON `newMessage` lines).

---

## Service Overview

WAF (Write Amplification Factor) is a feature embedded in the `diagnostic` C++ service. It monitors eMMC and SSD storage health by periodically reading raw hardware counters (host write bytes, NAND erase counts, bad blocks) using vendor-specific CLE tools, computing the WAF ratio, and persisting results to a SQLite database (`emmc_health.db` or `ssd_health.db`). After each write to the primary DB, WAF creates or refreshes a backup DB in WAL (Write-Ahead Log) mode. The health Python service subscribes to WAF metrics via MSGQ and writes structured JSON `newMessage` payloads to `health.log`; these same metrics appear as `critical_event_infoString[phm]` entries in health.log when uploaded.

**Host process:** `diagnostic` (C++ service)
**WAF thread created by:** `WAF_U: ... Creating thread for WAF`
**Primary config section:** `[diagnostic]` (in `bagheera_config.ini` / `bagheera_override.ini`)
**Default config values:**
- `waf_enabled`: not set in override → defaults to enabled (WAF thread starts)
- `waf_diag_interval_time`: `30` (minutes between WAF checks)

---

## Log Locations

WAF has **no dedicated log folder**. All WAF output is split across two log files:

| Log File | Format | What it contains |
|---|---|---|
| `diagnostic.log` | `<epoch_ms>: <uptime_ms>: <TAG>: <level>: <pid>: <tid>: <message>` | WAF thread init, DB open/backup/WAL, erase/write raw values, `Waf is <N>`, corruption/restoration messages |
| `health.log` | `YYYY-MM-DD HH:MM:SS,mmm - health - INFO - <message>` | `newMessage: {..., 'waf': N, 'erase_count': N, 'host_write_byte': N, ...}` JSON payloads; `critical_event_infoString[phm]` entries |

**Log paths by device type:**

| Device Type | diagnostic.log path | health.log path |
|---|---|---|
| krait / krait2 | `/data/nd_files/log/diagnostic/` | `/data/nd_files/log/health/health.log` |
| bagheera2 / bagheera3 / octo | `/home/ubuntu/.nddevice/log/diagnostic/` | `/home/ubuntu/.nddevice/log/health/health.log` |

---

## Database Paths

| Device Type | Primary DB | Backup DB |
|---|---|---|
| krait / krait2 | `/data/nd_files/db/emmc_health.db` | `/data/nd_files/db/emmc_health_backup.db` |
| bagheera2 / bagheera3 | `/home/ubuntu/.nddevice/db/emmc_health.db` | `/home/ubuntu/.nddevice/db/emmc_health_backup.db` |
| octo | `/home/ubuntu/.nddevice/db/ssd_health.db` | `/home/ubuntu/.nddevice/db/ssd_health_backup.db` |

**DB Tables and required columns:**

| Table | Devices | Required Columns |
|---|---|---|
| `int_emmc_health_info` | krait, krait2, bagheera2, bagheera3 | `row_index`, `host_write_data`, `erase_count`, `timestamp`, `waf` |
| `ext_emmc_health_info` | bagheera2, bagheera3 only | `row_index`, `host_write_data`, `erase_count`, `timestamp`, `waf` |
| `ssd_health_info` | octo only | `row_index`, `host_read_data`, `host_write_data`, `erase_count`, `timestamp` |

---

## Log Format Examples (from device 103452403525, diagnostic service PID 5953)

**diagnostic.log — WAF init and normal cycle:**
```
1779359925435: 34930: CFG_PRSR: I: 5953: 5953: No value present for key waf_diag_interval_time in override dictionary
1779359925452: 34947: DIAG: I: 5953: 5953: waf_diag_interval_time: 30
1779359928808: 38303: WAF_U: I: 5953: 5953: Creating thread for WAF
1779359928833: 38328: CFG_PRSR: I: 5953: 8035: No value present for key waf_enabled in override dictionary
1779359928854: 38349: WAF_U: I: 5953: 8035: DB Exists and is opened successfully
1779359928906: 38401: WAF_U: I: 5953: 8035: manufacturer is 0
1779359929014: 38509: NDMU: I: 5953: 5953: permissions changed: 0
1779359929719: 39214: MMC_CMDS: I: 5953: 8035: CMD62 and CMD63 read successfully from /dev/mmcblk0
1779359929722: 39217: MMC_CMDS: I: 5953: 8035: Erase count and write data are the same as last recorded, skipping WAF update. Last WAF: 1.832683
1779359929729: 39224: WAF_U: I: 5953: 8035: BACKUP_SOURCE: Backup database already exists. Proceeding with backup.
1779359929778: 39273: WAF_U: I: 5953: 8035: WAL Check: Journal mode: wal
1779359929778: 39273: WAF_U: I: 5953: 8035: Database set to WAL mode.
1779359929778: 39273: WAF_U: I: 5953: 8035: WAL checkpoint completed successfully.
1779359929808: 39303: WAF_U: I: 5953: 8035: Waf is 1.832683
1779359929812: 39307: WAF_U: I: 5953: 8035: Success in retrieving the WAF after retrying
1779359929828: 39323: WAF_U: I: 5953: 8035: Checking eMMC critical events for Int eMMC
1779359929849: 39344: WAF_U: I: 5953: 8035: DB Exists and is opened successfully
1779359929874: 39369: WAF_U: I: 5953: 8035: manufacturer is 1
1779359929910: 39405: MMC_CMDS: I: 5953: 8035: Erase count and write data are the same as last recorded, skipping WAF update. Last WAF: 2.028841
1779359929910: 39405: WAF_U: I: 5953: 8035: BACKUP_SOURCE: Backup database already exists. Proceeding with backup.
1779359929949: 39444: WAF_U: I: 5953: 8035: Waf is 2.028841
1779359929951: 39446: WAF_U: I: 5953: 8035: Checking eMMC critical events for Ext eMMC
```

**diagnostic.log — raw eMMC values (SDCARD thread, every ~30 min):**
```
1779359987149: 96644: SDCARD: I: 5953: 5953: Executing SD card health check for kingston
1779359987152: 96647: SDCARD: I: 5953: 5953: KSI : Host Write Count : 9541747
1779359987153: 96648: SDCARD: I: 5953: 5953: KSI : Avg Erase Count MLC : 158
1779359987155: 96650: SDCARD: I: 5953: 5953: KSI : Total No. of Later Bad Blocks : 0
1779359987158: 96653: SDCARD: I: 5953: 5953: Executing EMMC health check for sandisk
1779359987899: 97394: SDCARD: I: 5953: 5953: WD : Cumulative Write Data Size In 100MB : 17851
1779359987899: 97394: SDCARD: I: 5953: 5953: WD : Avg Erase Count MLC : 218
1779359987899: 97394: SDCARD: I: 5953: 5953: WD : Bad Block Runtime MLC : 0
1779359954768: 64263: SDCARD: I: 5953: 7828: Internal emmc lifetime: 7 % completed.
1779359954777: 64272: SDCARD: I: 5953: 7828: EMMC health tool output. MLC cyle: 218 commulative write: 1743 GB
1779359954777: 64272: SDCARD: I: 5953: 7828:  manufacturer: 1 oem_tool_succeed_emmc: 1
```

**health.log — newMessage JSON payload (Python health service):**
```
2026-05-21 13:57:03,437 - health - INFO - newMessage: {'host_write_byte': 1785200, 'erase_count': 218, 'erase_count_slc_pool1': 2, 'erase_count_slc_pool4': 850, 'waf': 1.832682926829, 'bad_blocks_manufactured': 5, 'bad_block_overall': 5, 'total_voltage_drops': 57, 'power_drops': 0, 'emmc_size': 15028, 'vendor': 'WesternDigital', 'firmware_version': '0x3733313033353137', 'uecc_count': 0, 'timestamp': 1779371823304}
2026-05-21 13:57:03,487 - health - INFO - newMessage: {'host_write_byte': 9542001, 'erase_count': 158, 'erase_count_slc_pool1': 2535, 'waf': 2.028840764331, 'bad_blocks_manufactured': 18, 'bad_block_overall': 18, 'temperature': 26, 'power_up_counter': 3193, 'power_drops': 63, 'emmc_size': 119448, 'vendor': 'KINGSTON', 'firmware_version': '0x5e00000000000000', 'uecc_count': 0, 'timestamp': 1779371823441}
```

**health.log — critical_event_infoString (sent to cloud):**
```
2026-05-21 13:57:36,415 - health - INFO - critical_event_infoString[phm]: {'timestamp': 1779371823304, 'process_name': 'DIAG', 'code': 130005, 'code_aux': 69, 'sys_uptime': 67997, 'desc': 'Int eMMC - WesternDigital : Host Write Byte: 1743 GB , Erase Count: 218 , WAF : 1.832683', 'count': 1}
2026-05-21 13:57:36,415 - health - INFO - critical_event_infoString[phm]: {'timestamp': 1779371823441, 'process_name': 'DIAG', 'code': 130005, 'code_aux': 100112, 'sys_uptime': 68134, 'desc': 'Ext eMMC - KINGSTON : Host Write Byte: 9318 GB , Erase Count: 158 , WAF : 2.028841', 'count': 1}
```

---

## Service Flows

### Flow 1: Config Parsing & WAF Thread Initialization

**What happens:** At diagnostic service startup, the config parser reads `waf_diag_interval_time` from `bagheera_config.ini` (overridden by `bagheera_override.ini` if present). If the key is absent from the override file, CFG_PRSR logs "No value present for key waf_diag_interval_time in override dictionary" and the compiled default (30 minutes) is used. DIAG then logs the resolved value: "waf_diag_interval_time: 30". If `waf_enabled=0` is set in the override, WAF logs "WAF Support is disabled From the Config...Exiting" and the thread exits immediately without opening any DB. If WAF is enabled (default), the WAF_U thread is created and opens the primary DB.

**When active:** Always at every diagnostic service startup
**Frequency:** Once at boot / diagnostic restart
**Cross-service impact:** `waf_diag_interval_time` controls how often the WAF cycle runs; short values (2 min) are used in tests to force a cycle without long waits

**Key log patterns (enabled — default):**
```
CFG_PRSR: I: ... No value present for key waf_diag_interval_time in override dictionary
DIAG: I: ... waf_diag_interval_time: 30
WAF_U: I: ... Creating thread for WAF
CFG_PRSR: I: ... No value present for key waf_enabled in override dictionary
WAF_U: I: ... DB Exists and is opened successfully
```

**Key log patterns (disabled — waf_enabled=0):**
```
DIAG: I: ... waf_diag_interval_time: 30
WAF_U: I: ... WAF Support is disabled From the Config...Exiting
```
Negative: `Waf is` must NOT appear when disabled.

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks |
|---|---|---|
| `TC_waf_2064` | `tests/waf/test_tc_waf_2064_validate_config_flow.py` | Push `waf_enabled=1`, `waf_diag_interval_time=2` → "waf_diag_interval_time: 2" in diagnostic.log + "Waf is" present |
| `TC_waf_2079` | `tests/waf/test_tc_waf_2079_validate_config_flow_once_disabled.py` | Push `waf_enabled=0` → "WAF Support is disabled From the Config...Exiting" in diagnostic.log + "waf is" must NOT appear |

---

### Flow 2: DB File Permissions, Schema Validation, and Size Check

**What happens:** After opening the primary DB, WAF sets file permissions via `NDMU` ("permissions changed: 0" = success). The DB is owned by `root:root` with mode `-rw-r--r--` (644). The DB contains tables specific to the device type: `int_emmc_health_info` on eMMC devices, `ext_emmc_health_info` on bagheera2/3 (external eMMC/SD card), and `ssd_health_info` on octo. Each table is validated via `PRAGMA table_info(<table>)`. The DB file must not exceed 6 MB (6291456 bytes) to prevent unbounded disk growth.

**When active:** Always when `waf_enabled=1` (or default)
**Frequency:** DB opened once per WAF cycle; permissions set at open
**Cross-service impact:** DB file lives on the same partition as other service databases — excessive size would consume shared disk space

**Key log patterns:**
```
WAF_U: I: ... DB Exists and is opened successfully
NDMU: I: ... permissions changed: 0
```

**Verification commands:**
```bash
# Permissions check
ls -l <db_path>
# Expected: -rw-r--r-- 1 root root ...

# Schema check (int eMMC)
sqlite3 <db_path> "PRAGMA table_info(int_emmc_health_info);"
# Required columns: row_index, host_write_data, erase_count, timestamp, waf

# Schema check (ext eMMC — bagheera2/3 only)
sqlite3 <db_path> "PRAGMA table_info(ext_emmc_health_info);"
# Required columns: row_index, host_write_data, erase_count, timestamp, waf

# Schema check (SSD — octo only)
sqlite3 <db_path> "PRAGMA table_info(ssd_health_info);"
# Required columns: row_index, host_read_data, host_write_data, erase_count, timestamp

# File size check
stat -c %s <db_path>
# Must be < 6291456 bytes (6 MB)
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks |
|---|---|---|
| `TC_waf_2071` | `tests/waf/test_tc_waf_2071_validate_dbfile_permission_and_fields.py` | DB permissions `-rw-r--r-- root root`; PRAGMA table_info for int_emmc_health_info (all eMMC), ext_emmc_health_info (bagheera2/3), ssd_health_info (octo) — all required columns present |
| `TC_waf_2138` | `tests/waf/test_tc_waf_2138_validate_file_size_of_emmc_health_db.py` | `stat -c %s <db_path>` < 6291456 bytes |

---

### Flow 3: Internal and External eMMC Data Appending to DB

**What happens:** Each WAF cycle, the diagnostic service reads hardware counters from the eMMC using CLE tools (sandisk: `cle smart_report_7250`; kingston: `mmc-utils-kingston`). The raw values (host_write_byte, erase_count, bad_block_count) are compared against the last recorded values in the DB. If they are unchanged, WAF logs "Erase count and write data are the same as last recorded, skipping WAF update. Last WAF: N" and skips writing a new row. If values changed, a new row is inserted into `int_emmc_health_info` (internal) and `ext_emmc_health_info` (external, bagheera2/3 only). The health Python service receives these values via MSGQ and logs them as `newMessage` JSON in health.log.

**When active:** Every `waf_diag_interval_time` minutes when `waf_enabled=1`
**Frequency:** Every 30 min (default) or every 2 min (test config)
**Cross-service impact:** `health` Python service receives data via MSGQ at ipc:///dev/shm/MSGQ/9355 and writes health.log

**Key log patterns — diagnostic.log:**
```
MMC_CMDS: I: ... CMD62 and CMD63 read successfully from /dev/mmcblk0
MMC_CMDS: I: ... Erase count and write data are the same as last recorded, skipping WAF update. Last WAF: 1.832683
SDCARD: I: ... KSI : Host Write Count : 9541747
SDCARD: I: ... KSI : Avg Erase Count MLC : 158
SDCARD: I: ... WD : Cumulative Write Data Size In 100MB : 17851
SDCARD: I: ... WD : Avg Erase Count MLC : 218
SDCARD: I: ... Internal emmc lifetime: 7 % completed.
SDCARD: I: ... EMMC health tool output. MLC cyle: 218 commulative write: 1743 GB
SDCARD: I: ... manufacturer: 1 oem_tool_succeed_emmc: 1
```

**Key log patterns — health.log (newMessage JSON):**
```
health - INFO - newMessage: {'host_write_byte': 1785200, 'erase_count': 218, ..., 'waf': 1.832682926829, ..., 'vendor': 'WesternDigital', 'timestamp': 1779371823304}
health - INFO - newMessage: {'host_write_byte': 9542001, 'erase_count': 158, ..., 'waf': 2.028840764331, ..., 'vendor': 'KINGSTON', 'timestamp': 1779371823441}
```

**DB query to verify data appended:**
```bash
# Verify host_write_byte from health.log is present in DB
sqlite3 <db_path> "SELECT * FROM int_emmc_health_info WHERE host_write_data = <host_write_byte>;"
sqlite3 <db_path> "SELECT * FROM ext_emmc_health_info WHERE host_write_data = <host_write_byte>;"
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks |
|---|---|---|
| `TC_waf_2084` | `tests/waf/test_tc_waf_2084_validate_int_emmc_data_appending_to_db.py` | Parse `host_write_byte` + `erase_count` + `timestamp` from health.log `newMessage`; verify same row exists in `int_emmc_health_info` DB via `SELECT * WHERE host_write_data = <N>` (skip: octo) |
| `TC_waf_2085` | `tests/waf/test_tc_waf_2085_validate_ext_emmc_data_append_to_db.py` | Same pattern for `ext_emmc_health_info` table (bagheera2/3 only; skip krait, krait2, octo) |

---

### Flow 4: Backup DB Creation, WAL Mode, and Backup Schema

**What happens:** After each WAF cycle writes to the primary DB, WAF creates or updates the backup DB. If the backup already exists, it logs "BACKUP_SOURCE: Backup database already exists. Proceeding with backup." WAF sets the DB to WAL (Write-Ahead Log) journal mode for atomic writes, logs "WAL Check: Journal mode: wal", "Database set to WAL mode.", and "WAL checkpoint completed successfully." The backup DB must have the same table schema as the primary DB. If the backup DB itself is detected as corrupted (sqlite3 error on open), WAF logs "Backup database is corrupted: file is not a database. Deleting and creating a new one." and recreates it.

**When active:** Every WAF cycle when `waf_enabled=1`
**Frequency:** Every `waf_diag_interval_time` minutes
**Cross-service impact:** Backup DB is the restore source for Flow 6 (primary corruption recovery)

**Key log patterns (normal — backup already exists):**
```
WAF_U: I: ... BACKUP_SOURCE: Backup database already exists. Proceeding with backup.
WAF_U: I: ... WAL Check: Journal mode: wal
WAF_U: I: ... Database set to WAL mode.
WAF_U: I: ... WAL checkpoint completed successfully.
```

**Key log patterns (backup corrupted):**
```
WAF_U: I: ... Backup database is corrupted: file is not a database. Deleting and creating a new one.
WAF_U: I: ... Backup database already exists. Proceeding with backup.
```

**Backup DB schema verification:**
```bash
sqlite3 <backup_db_path> "PRAGMA table_info(int_emmc_health_info);"
# Required: row_index, host_write_data, erase_count, timestamp, waf
sqlite3 <backup_db_path> "PRAGMA table_info(ext_emmc_health_info);"
# Required: row_index, host_write_data, erase_count, timestamp, waf
sqlite3 <backup_db_path> "PRAGMA table_info(ssd_health_info);"  # octo only
# Required: row_index, host_read_data, host_write_data, erase_count, timestamp
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks |
|---|---|---|
| `TC_waf_2106` | `tests/waf/test_tc_waf_2106_validate_backup_db_has_required_fields.py` | PRAGMA table_info on backup DB for all three tables (device-type-appropriate) — all required columns present |
| `TC_waf_2229` | `tests/waf/test_tc_waf_2229_validate_backup_db_corruption.py` | Corrupt backup DB → restart diagnostic → "Backup database is corrupted: file is not a database. Deleting and creating a new one." + "Backup database already exists. Proceeding with backup." in diagnostic.log |

---

### Flow 5: WAF Value Calculation (Avg Erase + Cumulative Write + WAF)

**What happens:** The WAF value is computed as `nand_write_data / host_write_data`. After computation, WAF_U logs "Waf is <N>" and "Success in retrieving the WAF after retrying". The raw inputs come from device-specific CLE tools: on bagheera2/3 the internal eMMC uses `cle smart_report_7250` (WesternDigital) which reports "Avg Erase Count MLC" and "Cumulative Write Data Size In 100MB"; on krait/krait2 the POOL3 erase count is used; on octo, SSD metrics ("Total Bytes Written", "SLC SuperBlock Avg EraseCount", "TLC SuperBlock Avg EraseCount") are used instead. All computed values are propagated to health.log as `newMessage` JSON and as `critical_event_infoString[phm]` entries.

**When active:** Every WAF cycle when `waf_enabled=1`
**Frequency:** Every `waf_diag_interval_time` minutes
**Cross-service impact:** WAF value is sent to cloud via health service `critical_event_infoString[phm]` (event code 130005)

**Key log patterns — diagnostic.log:**
```
WAF_U: I: ... Waf is 1.832683
WAF_U: I: ... Success in retrieving the WAF after retrying
WAF_U: I: ... Checking eMMC critical events for Int eMMC
WAF_U: I: ... Checking eMMC critical events for Ext eMMC
SDCARD: I: ... WD : Cumulative Write Data Size In 100MB : 17851
SDCARD: I: ... WD : Avg Erase Count MLC : 218
SDCARD: I: ... KSI : Avg Erase Count MLC : 158
```

**Key log patterns — health.log (critical event):**
```
health - INFO - critical_event_infoString[phm]: {'process_name': 'DIAG', 'code': 130005, 'code_aux': 69, 'desc': 'Int eMMC - WesternDigital : Host Write Byte: 1743 GB , Erase Count: 218 , WAF : 1.832683', ...}
health - INFO - critical_event_infoString[phm]: {'process_name': 'DIAG', 'code': 130005, 'code_aux': 100112, 'desc': 'Ext eMMC - KINGSTON : Host Write Byte: 9318 GB , Erase Count: 158 , WAF : 2.028841', ...}
```

**Device-specific raw metric patterns:**
| Device | Tool | Pattern in diagnostic.log |
|---|---|---|
| bagheera2/3 int eMMC (WesternDigital/SanDisk) | `cle smart_report_7250` | `WD : Avg Erase Count MLC : N` / `WD : Cumulative Write Data Size In 100MB : N` |
| bagheera2/3 ext eMMC (Kingston) | `mmc-utils-kingston` | `KSI : Avg Erase Count MLC : N` / `KSI : Host Write Count : N` |
| krait/krait2 | mmc tools | `Avg Erase Count POOL3 : N` |
| octo | NVMe/SSD tools | `Total Bytes Written`, `SLC SuperBlock Avg EraseCount`, `TLC SuperBlock Avg EraseCount` |

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks |
|---|---|---|
| `TC_waf_2163` | `tests/waf/test_tc_waf_2163_validate_avg_erase_cumulative_write_data_size_and_waf.py` | Parse "Cumulative Write Data Size In 100MB : N" (skip octo), "Avg Erase Count MLC : N" (bagheera) or "POOL3" (krait), "waf is N" — all must be non-zero numeric values in diagnostic.log |
| `TC_waf_2240` | `tests/waf/test_tc_waf_2240_validates_db_corruption_in_diagnostic.py` | Corrupt primary DB → restart diagnostic → "Database is corrupted: file is not a database" logged AND "Signal .* received from thread" must NOT appear (no crash) |

---

### Flow 6: DB Corruption Detection and Recovery

**What happens:** At the start of each WAF cycle, WAF attempts to open the primary DB. If the file is corrupted (e.g., truncated or overwritten with garbage), sqlite3 returns an error and WAF logs "Database is corrupted: file is not a database". WAF then restores from the backup DB and logs "Database successfully restored from backup". After restoration, it proceeds normally: "Backup completed successfully". If both the primary and backup DBs are corrupted or deleted, WAF recreates both from scratch with the correct schema (verified via PRAGMA table_info after recreation).

**When active:** Triggered when primary DB is corrupted or missing
**Frequency:** Per WAF cycle — detected at DB open
**Cross-service impact:** Restored DB retains all historical erase/write data; recreation from scratch starts fresh history

**Key log patterns (primary corrupted → restore from backup):**
```
WAF_U: ... Database is corrupted: file is not a database
WAF_U: ... Database successfully restored from backup
WAF_U: ... Backup database already exists. Proceeding with backup.
WAF_U: ... Backup completed successfully
```

**Key log patterns (both corrupted/deleted → recreate):**
```
WAF_U: ... Database is corrupted: file is not a database
WAF_U: ... Backup database is corrupted: file is not a database. Deleting and creating a new one.
WAF_U: ... DB Exists and is opened successfully
```
After recreation, PRAGMA table_info must return all required columns.

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks |
|---|---|---|
| `TC_waf_2152` | `tests/waf/test_tc_waf_2152_validates_db_corruption_and_restoration.py` | Corrupt primary DB → restart diagnostic → "Database is corrupted: file is not a database" + "Database successfully restored from backup" + "Backup database already exists. Proceeding with backup." + "Backup completed successfully" all present in diagnostic.log |
| `TC_waf_2161` | `tests/waf/test_tc_waf_2161_validate_restoration_of_corrupted_and_deleted_db_files.py` | Corrupt AND delete both primary and backup DBs → restart diagnostic → PRAGMA table_info on recreated DBs shows all required columns (int_emmc_health_info, ext_emmc_health_info, ssd_health_info per device type) |

---

### Flow 7: WAF Stability Across Reboots, Crashes, and No-Internet

**What happens:** WAF data persists across reboots because the DB lives on persistent storage. TC_2166 triggers an outward camera crash via GPIO (bagheera2/3/octo only), causing a `DBSTATE_SHUTDOWN_CAM_CRASH:REBOOT` shutdown — after device recovers, WAF must still produce "waf is N" in diagnostic.log. TC_2167 enables cyclic reboot (`cyclic_reboot_duration=2` min), waits for the device to go through a `DBSTATE_SHUTDOWN_CYCLIC:REBOOT` cycle, then verifies WAF value is still readable. TC_2168 disables internet (stops wifi_mgr, brings wlan0 down) and confirms WAF operates purely locally — "waf is N" must appear even with `ping 8.8.8.8` failing, confirming WAF does not depend on network connectivity.

**When active:** After any reboot (cam crash, cyclic, or normal) or while internet is down
**Frequency:** Per event
**Cross-service impact:** power_monitor records reboot reason; conn_mgr / wifi_mgr control internet; WAF must be independent of both

**Key log patterns (cam crash reboot — TC_2166):**
```
(power_mon logs) POWER_MONITOR_ctx->previous_shutdown_reason DBSTATE_SHUTDOWN_CAM_CRASH:REBOOT
(diagnostic.log) WAF_U: ... Waf is <N>
```

**Key log patterns (cyclic reboot — TC_2167):**
```
(power_mon logs) POWER_MONITOR_ctx->previous_shutdown_reason DBSTATE_SHUTDOWN_CYCLIC:REBOOT
(diagnostic.log) WAF_U: ... Waf is <N>
```

**Key log patterns (no internet — TC_2168):**
```
(device shell) ping -c 3 -W 3 8.8.8.8 → "Network is unreachable"
(diagnostic.log) WAF_U: ... Waf is <N>
```

**GPIO crash commands by device type (TC_2166):**
| Device | GPIO command |
|---|---|
| bagheera2 | `gpio_test -n 461 -o 0` |
| bagheera3 | `gpio_test -n 243 -o 0` |
| octo | `gpio_test -n 202 -o 0` |

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks |
|---|---|---|
| `TC_waf_2166` | `tests/waf/test_tc_waf_2166_outward_cam_crash_reboot_behaviour.py` | GPIO crash → "dbstate_shutdown_cam_crash:reboot" in power_mon logs + "waf is" in diagnostic.log post-reboot (bagheera2/3/octo; skip krait/krait2) |
| `TC_waf_2167` | `tests/waf/test_tc_waf_2167_validate_waf_value_and_db_post_cyclic_reboot.py` | Enable `cyclic_reboot_duration=2` → "dbstate_shutdown_cyclic:reboot" in power_mon logs + "waf is" in diagnostic.log post-reboot |
| `TC_waf_2168` | `tests/waf/test_tc_waf_2168_validate_waf_without_internet.py` | Stop wifi_mgr + `ip link set wlan0 down` → `ping 8.8.8.8` fails → "waf is" still appears in diagnostic.log; restore internet after |

---

## Config-Driven Flow Activation

The agent MUST read the device config from `device_data/device_<ID>_config.ini` before selecting test cases. Use the following mapping:

| Config Section | Config Key | Value | Activates Flow(s) | Test Cases Affected |
|---|---|---|---|---|
| `[diagnostic]` | `waf_enabled` | `1` or absent (default) | Flows 2–7 (all WAF active flows) | all TCs except TC_waf_2079 |
| `[diagnostic]` | `waf_enabled` | `0` | Flow 1 disabled path only | TC_waf_2079 |
| `[diagnostic]` | `waf_diag_interval_time` | `2` (test value) | Flows 3, 5, 6 — forces quick cycle | TC_waf_2064, 2084, 2085, 2152, 2161, 2163, 2166, 2167, 2168, 2229, 2240 |
| `[diagnostic]` | `waf_diag_interval_time` | `30` (default) | Flows 3, 5 — normal polling | TC_waf_2071, 2106, 2138 |
| `[power_monitor]` | `cyclic_reboot_duration` | `2` | Flow 7 cyclic reboot | TC_waf_2167 |
| device_type | `octo` | — | `ssd_health_info` table; no ext eMMC | TC_waf_2071, 2106, 2161 (octo branch) |
| device_type | `bagheera2`, `bagheera3` | — | `ext_emmc_health_info` table active | TC_waf_2085, 2071, 2106, 2161 (ext branch) |
| device_type | `krait`, `krait2` | — | No ext eMMC; POOL3 erase count | TC_waf_2085 skipped; TC_waf_2163 uses POOL3 pattern |
| device_type | `krait`, `krait2` | — | No GPIO crash | TC_waf_2166 skipped |

**Rules:**
- `waf_enabled=0` → only TC_waf_2079 applies; all other WAF TCs are NOT_TRIGGERED
- `waf_enabled=1` or absent → run all other TCs (skipping device-type mismatches)
- Tests that push `waf_diag_interval_time=2` via config override do so within the test itself — no pre-condition needed
- Device-type restrictions are enforced inside each test; the agent should note them when reporting

---

## Cross-Service Dependencies

| Related Service | Why | When to check its logs |
|---|---|---|
| `diagnostic` | WAF runs as a thread inside diagnostic; ALL WAF C++ logs are in `diagnostic.log` | Every flow — primary log source |
| `health` (Python service) | Subscribes to WAF metrics via MSGQ; writes `newMessage` JSON and `critical_event_infoString[phm]` to `health.log` | Flow 3 (data appending), Flow 5 (WAF value in cloud event) |
| `power_monitor` | Records shutdown reason (`DBSTATE_SHUTDOWN_CAM_CRASH:REBOOT`, `DBSTATE_SHUTDOWN_CYCLIC:REBOOT`) | Flow 7 (TC_2166, TC_2167) |
| `conn_mgr` / `wifi_mgr` | Internet connectivity — TC_2168 stops wifi_mgr to simulate no-network | Flow 7 (TC_2168) |

---

## Flow Dependency Graph

```
diagnostic service start
 └─► [Flow 1: Config Parsing]
       ├─► waf_enabled=0 → "WAF Support is disabled...Exiting" (TC_2079)
       └─► waf_enabled=1 (default)
             └─► WAF_U thread created
                   └─► [Flow 2: DB open + permissions + schema + size check] (TC_2071, 2138)
                   └─► [Flow 4: Backup DB create/WAL mode] (TC_2106, 2229)
                   └─► every waf_diag_interval_time minutes:
                         └─► [Flow 3: eMMC hw counter read → DB append] (TC_2084, 2085)
                               └─► health.log newMessage JSON (TC_2084, 2085)
                         └─► [Flow 5: WAF value compute → Waf is N] (TC_2163, 2240)
                               └─► health.log critical_event_infoString (cloud)
                         └─► [Flow 4: WAL checkpoint + backup sync]
                   └─► on DB corruption:
                         └─► [Flow 6: Restore from backup OR recreate] (TC_2152, 2161)

reboot events (cam crash / cyclic / normal)
 └─► [Flow 7: Post-reboot WAF still works] (TC_2166, 2167, 2168)

internet disabled
 └─► [Flow 7: WAF works without network] (TC_2168)
```

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`; check `[diagnostic] waf_enabled`
2. **If `waf_enabled=0`**: only TC_waf_2079 applies — check for disabled log pattern; skip all others
3. **If `waf_enabled=1` or absent**: all flows active — proceed with device-type filtering
4. **Determine device type** to select correct DB path, backup DB path, table names, and device-skipped TCs
5. **For log searches**, use `diagnostic.log` for all `WAF_U:`, `MMC_CMDS:`, `SDCARD:`, `DIAG:` tagged lines; use `health.log` for `newMessage` JSON and `critical_event_infoString` lines
6. **For DB queries**, run `sqlite3 <db_path> "PRAGMA table_info(<table>);"` and `SELECT` queries as defined per flow
7. **For Flow 7 stability tests**, also check `power_monitor` logs for shutdown reason strings
8. **Note**: "Erase count and write data are the same as last recorded, skipping WAF update" is **normal and non-fatal** — it means eMMC counters did not change since last boot; `Waf is N` still appears from the cached last value
9. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED
