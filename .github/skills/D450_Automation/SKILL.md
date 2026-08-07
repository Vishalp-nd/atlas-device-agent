```skill
---
name: D450_Automation
description: "Use when: running device sanity or test cases on a D450 (bagheera3) device. Provides all config INI paths, log paths, DB paths, partition layout, and serial console connection details so the device-sanity agent can resolve file locations without guessing."
argument-hint: "device serial port (e.g., /D450_Automation /dev/ttyACM0)"
---

# D450 Automation — Device Path Reference

Complete path reference for the **D450** product line (`devicetype=bagheera3`). Use this skill to resolve file locations when running generic test cases against a D450 device.

## Device Connection

The D450 connects via **serial console** — **not** ADB. In the LangGraph workflow, the `SerialTransport` handles this automatically. Use `device_shell("command")` to run commands — do NOT wrap in `serial_conn.py`.

| Property | Value |
|----------|-------|
| Connection Method | Serial (via `SerialTransport`) |
| Default Serial Port | `/dev/ttyACM0` |
| Baud Rate | `115200` |
| Username | `ubuntu` |
| Password | `AaVPBf-hl6qqRJLA` |

### Running Commands on D450

In the LangGraph workflow, just pass the raw device command to `device_shell()`:

```
device_shell("cat /home/ubuntu/.nddevice/nddevice.ini")
device_shell("systemctl is-active bagheera")
device_shell("cat /home/ubuntu/config/deviceconfig.ini")
```

### Log Searching on D450

When using the `grep_logs` tool on D450, specify `log_root="/home/ubuntu/.nddevice/log"` — the default `/data/nd_files/log` does NOT exist on D450.

### File Transfer (D450)

Since there is no ADB, file transfers use `device_shell()` for direct on-device editing:

| Operation | Method |
|-----------|--------|
| Read file | `device_shell("cat /path/to/file")` |
| Write file | `device_shell("echo 'content' > /path/to/file")` |
| Modify config | `device_shell("sed -i 's/old/new/' /path/to/file")` |
| Push config override | Use `ensure_config()` tool or `device_shell()` with echo/sed |

## Device Identification

| Property | Value |
|----------|-------|
| Device Type | `bagheera3` |
| Device Subtype | `dvt2` |
| Product Line | D450 |
| OS | Ubuntu 18.04.6 LTS (Bionic Beaver) on NVIDIA Tegra |
| Hostname | `tegra-ubuntu` |
| Manifest Path | `/home/ubuntu/.nddevice/latest/package_manifest.ini` |
| Device Config | `/home/ubuntu/config/deviceconfig.ini` |

Detect device type:
```bash
grep -i devicetype /home/ubuntu/config/deviceconfig.ini
# → devicetype                     = bagheera3
```

Read device identity (deviceId, sessionId, deviceSubType):

```bash
cat /home/ubuntu/config/deviceconfig.ini
```

## Config INI Paths

### Active Config (read-only — shipped with OTA)

The `latest` symlink points to the currently active OTA version directory.
`latest` → `/home/ubuntu/.nddevice/<OTA_VERSION>/` (e.g., `/home/ubuntu/.nddevice/5.6.13.rc.2/`)

| File                                       | Path                                                           |
| ------------------------------------------ | -------------------------------------------------------------- |
| Package Manifest                           | `/home/ubuntu/.nddevice/latest/package_manifest.ini`         |
| Bagheera Config                            | `/home/ubuntu/.nddevice/latest/bagheera_config.ini`          |
| ND Config (symlink →`nd_config_US.ini`) | `/home/ubuntu/.nddevice/latest/nd_config.ini`                |
| ND Config US                               | `/home/ubuntu/.nddevice/latest/nd_config_US.ini`             |
| ND Config CA                               | `/home/ubuntu/.nddevice/latest/nd_config_CA.ini`             |
| ND Config MX                               | `/home/ubuntu/.nddevice/latest/nd_config_MX.ini`             |
| ND Config Recovery                         | `/home/ubuntu/.nddevice/latest/nd_config_recovery.ini`       |
| ND Core Common                             | `/home/ubuntu/.nddevice/latest/nd_core_common.ini`           |
| Cloud Config                               | `/home/ubuntu/.nddevice/latest/cloudconfig.ini`              |
| SAM Config                                 | `/home/ubuntu/.nddevice/latest/sam_config.ini`               |
| Automation Config (OTA copy)               | `/home/ubuntu/.nddevice/latest/automation_config.ini`        |
| DMS Installer Config                       | `/home/ubuntu/.nddevice/latest/nd_dms_installer_config.ini`  |
| VBUS FW Version Skeleton                   | `/home/ubuntu/.nddevice/latest/vbus_fw_version_skeleton.ini` |

### Override / Runtime Config (writable)

All writable override/runtime configs reside under `/home/ubuntu/config/`.

| File                     | Path                                                          |
| ------------------------ | ------------------------------------------------------------- |
| Bagheera Override        | `/home/ubuntu/config/bagheera_override.ini`                 |
| Device Config            | `/home/ubuntu/config/deviceconfig.ini`                      |
| Automation Config        | `/home/ubuntu/config/automation_config.ini`                 |
| Camera Override          | `/home/ubuntu/config/cam_override.ini`                      |
| Log Config               | `/home/ubuntu/config/logconfig.ini`                         |
| Inward Video Calibration | `/home/ubuntu/config/inward_video_pipeline_calibration.ini` |
| Camera Obstruction       | `/home/ubuntu/config/cameraObstruction.json`                |
| Lane Calibration         | `/home/ubuntu/config/laneCal.json`                          |
| Conn Mgr Config          | `/home/ubuntu/config/conn_mgr_config.txt`                   |
| OBD Config               | `/home/ubuntu/config/obd_config.ini`                        |
| VBUS Common Config       | `/home/ubuntu/config/vbus_common_config.ini`                |
| VBUS FW Version          | `/home/ubuntu/config/vbus_fw_version.ini`                   |

> **Important:** On D450 (`bagheera3`), the override config is at `/home/ubuntu/config/bagheera_override.ini` — **NOT** at `/data/nd_files/config/` (that path does not exist on D450).

### Device State Config

| File                              | Path                                                   |
| --------------------------------- | ------------------------------------------------------ |
| NDDevice State (version, upgrade) | `/home/ubuntu/.nddevice/nddevice.ini`                |
| MDVR Config                       | `/home/ubuntu/.nddevice/mdvr_config.ini`             |
| OBD Config (nddevice copy)        | `/home/ubuntu/.nddevice/obd_config.ini`              |
| OBD Info                          | `/home/ubuntu/.nddevice/obd_info.txt`                |
| GPS Cache                         | `/home/ubuntu/.nddevice/gps_cache.json`              |
| Log File JSON                     | `/home/ubuntu/.nddevice/logfile.json`                |
| Power KeepAlive Response          | `/home/ubuntu/.nddevice/power_keepaliveresponse.txt` |
| Ext EMMC WAF Dict                 | `/home/ubuntu/.nddevice/Ext_EMMC_WAF_Dict.json`      |
| Int EMMC WAF Dict                 | `/home/ubuntu/.nddevice/Int_EMMC_WAF_Dict.json`      |
| Current Speed                     | `/home/ubuntu/.nddevice/current_speed.info`          |
| Previous Speed                    | `/home/ubuntu/.nddevice/previous_speed.info`         |
| Prev Valid Speed                  | `/home/ubuntu/.nddevice/prev_valid_speed.info`       |
| Prev Voltage                      | `/home/ubuntu/.nddevice/prev_volt.info`              |
| Battery Voltage                   | `/home/ubuntu/.nddevice/battery_voltage`             |
| Boot Status                       | `/home/ubuntu/.nddevice/boot_status`                 |
| Filter Included CSV               | `/home/ubuntu/.nddevice/filter_included.csv`         |
| Filter Included Dec CSV           | `/home/ubuntu/.nddevice/filter_included_dec.csv`     |
| Filter Less CSV                   | `/home/ubuntu/.nddevice/filter_less.csv`             |
| Reboot Trigger Script             | `/home/ubuntu/.nddevice/reboot_trigger.sh`           |

### Bootstrap Config (factory defaults)

| File                      | Path                                                     |
| ------------------------- | -------------------------------------------------------- |
| Bootstrap Bagheera Config | `/home/ubuntu/.nddevice/bootstrap/bagheera_config.ini` |
| Bootstrap Cloud Config    | `/home/ubuntu/.nddevice/bootstrap/cloudconfig.ini`     |

### Backup Config

| File             | Path                     |
| ---------------- | ------------------------ |
| Backup directory | `/home/ubuntu/backup/` |

Contains backup copies of: `bagheera_config.ini`, `cloudconfig.ini`, `deviceconfig.ini`, `nddevice.ini`, `nd_config.ini`, `nd_config_US.ini`, `nd_config_CA.ini`, `nd_config_MX.ini`, `nd_config_recovery.ini`, `start_recording.sh`, `stop_recording.sh`, ED25519 key pairs (`ed25519key.pem_*`, `pub-ed25519.pem_*`)

## Log Paths

### Base Log Directory

```
/home/ubuntu/.nddevice/log/
```

## Services (systemd)

All ND services on D450 (bagheera3) that must be `active (running)` for a healthy device:

```
analytics.service
apm.service
audio.service
awsiot.service
bagheera.service
btfv.service
cam_rec.service
canAnalyticsClient.service
circular_buffer.service
conn_mgr.service
deleter.service
deviceHealthClient.service
diagnostic.service
dmsAnalyticsClient.service
ext_cam.service
fancontrol.service
gps.service
health.service
inertialAnalyticsClient.service
inference.service
inference_inertial.service
installer_app.service
inwardAnalyticsClient.service
keep_alive_manager.service
nd_bt_cli.service
nd_dta.service
nd_sam.service
ndcentral.service
obd.service
otacheck.service
outwardAnalyticsClient.service
overspeedClient.service
power_mon.service
scheduler.service
scheduler_manager.service
service_mon.service
speed.service
svc.service
time_sync.service
unifiedAnalyticsClient.service
unifieduploader.service
updater.service
```

Check all services at once:

```bash
systemctl is-active analytics apm audio awsiot bagheera btfv cam_rec canAnalyticsClient circular_buffer conn_mgr deleter deviceHealthClient diagnostic dmsAnalyticsClient ext_cam fancontrol gps health inertialAnalyticsClient inference inference_inertial installer_app inwardAnalyticsClient keep_alive_manager nd_bt_cli nd_dta nd_sam ndcentral obd otacheck outwardAnalyticsClient overspeedClient power_mon scheduler scheduler_manager service_mon speed svc time_sync unifiedAnalyticsClient unifieduploader updater
```

### Service Log Subdirectories

Each service writes logs to its own subdirectory. The `_c` suffix directories contain critical logs.

| Service                 | Active Logs                                             | Critical Logs                                             |
| ----------------------- | ------------------------------------------------------- | --------------------------------------------------------- |
| analytics               | `/home/ubuntu/.nddevice/log/analytics/`               | `/home/ubuntu/.nddevice/log/analytics_c/`               |
| apm                     | `/home/ubuntu/.nddevice/log/apm/`                     | `/home/ubuntu/.nddevice/log/apm_c/`                     |
| audio                   | `/home/ubuntu/.nddevice/log/audio/`                   | `/home/ubuntu/.nddevice/log/audio_c/`                   |
| awsiot                  | `/home/ubuntu/.nddevice/log/awsiot/`                  | `/home/ubuntu/.nddevice/log/awsiot_c/`                  |
| btfv                    | `/home/ubuntu/.nddevice/log/btfv/`                    | `/home/ubuntu/.nddevice/log/btfv_c/`                    |
| cam_rec                 | `/home/ubuntu/.nddevice/log/cam_rec/`                 | `/home/ubuntu/.nddevice/log/cam_rec_c/`                 |
| canAnalyticsClient      | `/home/ubuntu/.nddevice/log/canAnalyticsClient/`      | `/home/ubuntu/.nddevice/log/canAnalyticsClient_c/`      |
| circ_buff               | `/home/ubuntu/.nddevice/log/circ_buff/`               | `/home/ubuntu/.nddevice/log/circ_buff_c/`               |
| conn_mgr                | `/home/ubuntu/.nddevice/log/conn_mgr/`                | `/home/ubuntu/.nddevice/log/conn_mgr_c/`                |
| conn_mgr_debug          | `/home/ubuntu/.nddevice/log/conn_mgr_debug/`          | —                                                        |
| deleter                 | `/home/ubuntu/.nddevice/log/deleter/`                 | `/home/ubuntu/.nddevice/log/deleter_c/`                 |
| deviceHealthClient      | `/home/ubuntu/.nddevice/log/deviceHealthClient/`      | `/home/ubuntu/.nddevice/log/deviceHealthClient_c/`      |
| diagnostic              | `/home/ubuntu/.nddevice/log/diagnostic/`              | `/home/ubuntu/.nddevice/log/diagnostic_c/`              |
| dmsAnalyticsClient      | `/home/ubuntu/.nddevice/log/dmsAnalyticsClient/`      | `/home/ubuntu/.nddevice/log/dmsAnalyticsClient_c/`      |
| ext_cam                 | `/home/ubuntu/.nddevice/log/ext_cam/`                 | `/home/ubuntu/.nddevice/log/ext_cam_c/`                 |
| fancontrol              | `/home/ubuntu/.nddevice/log/fan/`                     | `/home/ubuntu/.nddevice/log/fan_c/`                     |
| gps                     | `/home/ubuntu/.nddevice/log/gps/`                     | `/home/ubuntu/.nddevice/log/gps_c/`                     |
| health                  | `/home/ubuntu/.nddevice/log/health/`                  | `/home/ubuntu/.nddevice/log/health_c/`                  |
| inertialAnalyticsClient | `/home/ubuntu/.nddevice/log/inertialAnalyticsClient/` | `/home/ubuntu/.nddevice/log/inertialAnalyticsClient_c/` |
| inference               | `/home/ubuntu/.nddevice/log/inference/`               | `/home/ubuntu/.nddevice/log/inference_c/`               |
| inference_inertial      | `/home/ubuntu/.nddevice/log/inference_inertial/`      | `/home/ubuntu/.nddevice/log/inference_inertial_c/`      |
| installer_app           | `/home/ubuntu/.nddevice/log/installer_app/`           | `/home/ubuntu/.nddevice/log/installer_app_c/`           |
| inwardAnalyticsClient   | `/home/ubuntu/.nddevice/log/inwardAnalyticsClient/`   | `/home/ubuntu/.nddevice/log/inwardAnalyticsClient_c/`   |
| keep_alive_manager      | `/home/ubuntu/.nddevice/log/keep_alive_manager/`      | `/home/ubuntu/.nddevice/log/keep_alive_manager_c/`      |
| nd_app_reboot           | `/home/ubuntu/.nddevice/log/nd_app_reboot/`           | `/home/ubuntu/.nddevice/log/nd_app_reboot_c/`           |
| nd_bt_cli               | `/home/ubuntu/.nddevice/log/nd_bt_cli/`               | `/home/ubuntu/.nddevice/log/nd_bt_cli_c/`               |
| nd_dta                  | `/home/ubuntu/.nddevice/log/nd_dta/`                  | `/home/ubuntu/.nddevice/log/nd_dta_c/`                  |
| nd_sam                  | `/home/ubuntu/.nddevice/log/nd_sam/`                  | `/home/ubuntu/.nddevice/log/nd_sam_c/`                  |
| ndcentral               | `/home/ubuntu/.nddevice/log/ndcentral/`               | `/home/ubuntu/.nddevice/log/ndcentral_c/`               |
| obd                     | `/home/ubuntu/.nddevice/log/obd/`                     | `/home/ubuntu/.nddevice/log/obd_c/`                     |
| otacheck                | `/home/ubuntu/.nddevice/log/otacheck/`                | `/home/ubuntu/.nddevice/log/otacheck_c/`                |
| outwardAnalyticsClient  | `/home/ubuntu/.nddevice/log/outwardAnalyticsClient/`  | `/home/ubuntu/.nddevice/log/outwardAnalyticsClient_c/`  |
| overspeedClient         | `/home/ubuntu/.nddevice/log/overspeedClient/`         | `/home/ubuntu/.nddevice/log/overspeedClient_c/`         |
| power_mon               | `/home/ubuntu/.nddevice/log/power_mon/`               | `/home/ubuntu/.nddevice/log/power_mon_c/`               |
| reboot                  | `/home/ubuntu/.nddevice/log/reboot/`                  | `/home/ubuntu/.nddevice/log/reboot_c/`                  |
| scheduler               | `/home/ubuntu/.nddevice/log/scheduler/`               | `/home/ubuntu/.nddevice/log/scheduler_c/`               |
| scheduler_manager       | `/home/ubuntu/.nddevice/log/scheduler_manager/`       | `/home/ubuntu/.nddevice/log/scheduler_manager_c/`       |
| service_mon             | `/home/ubuntu/.nddevice/log/service_mon/`             | `/home/ubuntu/.nddevice/log/service_mon_c/`             |
| speed                   | `/home/ubuntu/.nddevice/log/speed/`                   | `/home/ubuntu/.nddevice/log/speed_c/`                   |
| svc                     | `/home/ubuntu/.nddevice/log/svc/`                     | `/home/ubuntu/.nddevice/log/svc_c/`                     |
| time_sync               | `/home/ubuntu/.nddevice/log/time_sync/`               | `/home/ubuntu/.nddevice/log/time_sync_c/`               |
| unifiedAnalyticsClient  | `/home/ubuntu/.nddevice/log/unifiedAnalyticsClient/`  | `/home/ubuntu/.nddevice/log/unifiedAnalyticsClient_c/`  |
| unifieduploader         | `/home/ubuntu/.nddevice/log/unifieduploader/`         | `/home/ubuntu/.nddevice/log/unifieduploader_c/`         |
| updater                 | `/home/ubuntu/.nddevice/log/updater/`                 | `/home/ubuntu/.nddevice/log/updater_c/`                 |
| uploader                | `/home/ubuntu/.nddevice/log/uploader/`                | `/home/ubuntu/.nddevice/log/uploader_c/`                |
| wifi_mgr                | `/home/ubuntu/.nddevice/log/wifi_mgr/`                | `/home/ubuntu/.nddevice/log/wifi_mgr_c/`                |

### Standalone Log Files

| File                         | Path                                                         |
| ---------------------------- | ------------------------------------------------------------ |
| Health Stats Log             | `/home/ubuntu/.nddevice/log/healthstats.log`               |
| Health Stats Upload Response | `/home/ubuntu/.nddevice/log/healthstatsUploadResponse.log` |
| Timer Log                    | `/home/ubuntu/.nddevice/log/timer.log`                     |
| Timer2 Log                   | `/home/ubuntu/.nddevice/log/timer2.log`                    |
| Analytics Log Error          | `/home/ubuntu/.nddevice/log/analytics_log_error.log`       |
| Analytics Service Log        | `/home/ubuntu/.nddevice/log/analyticsService_service.log`  |
| Bhcopy Log                   | `/home/ubuntu/.nddevice/log/bhcopy.log`                    |
| Wrapper OTACheck Log         | `/home/ubuntu/.nddevice/log/wrapper_otacheck.log`          |
| Wrapper Scheduler Log        | `/home/ubuntu/.nddevice/log/wrapper_scheduler.log`         |
| KeepAlive Count              | `/home/ubuntu/.nddevice/log/keepalive_count.txt`           |

### Log Archive

| Directory   | Path                                    |
| ----------- | --------------------------------------- |
| Log Archive | `/home/ubuntu/.nddevice/log/archive/` |

## Database Paths

### Databases under `/home/ubuntu/.nddevice/db/`

| Database              | Path                                                | Purpose                  |
| --------------------- | --------------------------------------------------- | ------------------------ |
| diagnostic.db         | `/home/ubuntu/.nddevice/db/diagnostic.db`         | Diagnostic data          |
| emmc_health.db        | `/home/ubuntu/.nddevice/db/emmc_health.db`        | eMMC health monitoring   |
| emmc_health_backup.db | `/home/ubuntu/.nddevice/db/emmc_health_backup.db` | eMMC health backup       |
| HEALTH.db             | `/home/ubuntu/.nddevice/db/HEALTH.db`             | Health data              |
| healthstats.db        | `/home/ubuntu/.nddevice/db/healthstats.db`        | Health statistics        |
| HERE.db               | `/home/ubuntu/.nddevice/db/HERE.db`               | HERE maps data           |
| keep_alive_mgr.db     | `/home/ubuntu/.nddevice/db/keep_alive_mgr.db`     | Keep-alive manager state |
| THRESHOLD.db          | `/home/ubuntu/.nddevice/db/THRESHOLD.db`          | Threshold configuration  |
| udid.db               | `/home/ubuntu/.nddevice/db/udid.db`               | Unique device identifier |

### Databases under `/home/ubuntu/.nddevice/` (root)

| Database           | Path                                          | Purpose                       |
| ------------------ | --------------------------------------------- | ----------------------------- |
| accessory.db       | `/home/ubuntu/.nddevice/accessory.db`       | Accessory/peripheral tracking |
| camera_crash.db    | `/home/ubuntu/.nddevice/camera_crash.db`    | Camera crash events           |
| circular_buffer.db | `/home/ubuntu/.nddevice/circular_buffer.db` | Circular buffer metadata      |
| ea.db              | `/home/ubuntu/.nddevice/ea.db`              | Event analytics               |
| ext_cam_config.db  | `/home/ubuntu/.nddevice/ext_cam_config.db`  | External camera configuration |
| gen_property.db    | `/home/ubuntu/.nddevice/gen_property.db`    | General device properties     |
| obd_property.db    | `/home/ubuntu/.nddevice/obd_property.db`    | OBD properties                |
| power_monitor.db   | `/home/ubuntu/.nddevice/power_monitor.db`   | Power monitoring data         |
| uploader.db        | `/home/ubuntu/.nddevice/uploader.db`        | Upload queue/state            |

### Databases under other subdirectories

| Database          | Path                                             | Purpose            |
| ----------------- | ------------------------------------------------ | ------------------ |
| BLE login.db      | `/home/ubuntu/.nddevice/.ble/login.db`         | BLE login data     |
| SAM CFD           | `/home/ubuntu/.nddevice/sam_db/sam_cfd.db`     | SAM CFD data       |
| SAM Gen           | `/home/ubuntu/.nddevice/sam_db/sam_gen.db`     | SAM general data   |
| Uploader (backup) | `/home/ubuntu/.nddevice/.uploader/uploader.db` | Uploader backup DB |

## Data Partition Layout

### Root filesystem (`/dev/mmcblk0p1` — 14 GB, mounted at `/`)

| Directory               | Path                                       | Purpose                                                                                                     |
| ----------------------- | ------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| Config                  | `/home/ubuntu/config/`                   | Override configs, logconfig, deviceconfig                                                                   |
| NDDevice Root           | `/home/ubuntu/.nddevice/`                | State files, DBs, logs, OTA packages                                                                        |
| Active OTA              | `/home/ubuntu/.nddevice/latest/`         | Symlink to current OTA version dir                                                                          |
| Database (sub)          | `/home/ubuntu/.nddevice/db/`             | Health, threshold, UDID, keep-alive DBs                                                                     |
| Logs                    | `/home/ubuntu/.nddevice/log/`            | All service logs                                                                                            |
| Log Archive             | `/home/ubuntu/.nddevice/log/archive/`    | Archived logs                                                                                               |
| Observations            | `/home/ubuntu/.nddevice/observations/`   | Analytics observations                                                                                      |
| Inertial Observations   | `/home/ubuntu/.nddevice/inertial_obs/`   | Inertial sensor observations                                                                                |
| Unoperated Observations | `/home/ubuntu/.nddevice/unoperated_obs/` | Unprocessed observations                                                                                    |
| Unoperated EA           | `/home/ubuntu/.nddevice/unoperated_ea/`  | Unprocessed event analytics                                                                                 |
| EA Zips                 | `/home/ubuntu/.nddevice/ea_zips/`        | Compressed EA archives                                                                                      |
| Certificates            | `/home/ubuntu/.nddevice/certificate/`    | TLS/AWS certs (`cacert.pem`, `certificate.pem.crt`, `private.pem.key`, `root-CA.crt`, ED25519 keys) |
| Cloud Response          | `/home/ubuntu/.nddevice/cloud_response/` | Cached cloud responses                                                                                      |
| MSGQ                    | `/home/ubuntu/.nddevice/MSGQ/`           | Message queue IPC                                                                                           |
| SAM DB                  | `/home/ubuntu/.nddevice/sam_db/`         | SAM databases                                                                                               |
| Sign Crops              | `/home/ubuntu/.nddevice/sign_crops/`     | Sign detection crops                                                                                        |
| Bootstrap               | `/home/ubuntu/.nddevice/bootstrap/`      | Factory default config & binaries                                                                           |
| Backup (nddevice)       | `/home/ubuntu/.nddevice/backup/`         | nddevice-level backups                                                                                      |
| BLE                     | `/home/ubuntu/.nddevice/.ble/`           | BLE login database                                                                                          |
| Uploader (backup)       | `/home/ubuntu/.nddevice/.uploader/`      | Uploader backup DB                                                                                          |
| AWS IoT Lib             | `/home/ubuntu/.nddevice/awsiot_lib/`     | AWS IoT libraries                                                                                           |
| Firmware                | `/home/ubuntu/.nddevice/firmware/`       | Device firmware (MDVR)                                                                                      |
| CLE Tool                | `/home/ubuntu/.nddevice/CLE_tool/`       | Card Life Estimator tool                                                                                    |
| Backup                  | `/home/ubuntu/backup/`                   | Config backup copies                                                                                        |
| Autocam                 | `/home/ubuntu/autocam/`                  | Autocam data                                                                                                |
| DM Logging              | `/home/ubuntu/dm_logging/`               | DM logging data                                                                                             |
| FW Download             | `/home/ubuntu/fw_download/`              | Firmware download staging                                                                                   |

### External eMMC (`/dev/mmcblk1` — 229 GB, mounted at `/media/data`)

| Directory       | Path                            | Purpose                       |
| --------------- | ------------------------------- | ----------------------------- |
| SD Card (video) | `/media/data/nd_sdcard/`      | Video circular buffer storage |
| NTDI Bag3 Logs  | `/media/data/ntdi_bag3_logs/` | NTDI bagheera3 logs           |

### Disk Partitions

| Partition           | Mount               | Size   | Purpose                                            |
| ------------------- | ------------------- | ------ | -------------------------------------------------- |
| `/dev/mmcblk0p1`  | `/`               | 14 GB  | Root filesystem (OS, configs, nddevice, logs, DBs) |
| `/dev/mmcblk1`    | `/media/data`     | 229 GB | External storage (video, nd_sdcard)                |
| `/dev/mmcblk0p32` | `/fru_check/tmp2` | 2.9 MB | FRU check partition                                |

## Constraints

- Do NOT modify files under `/home/ubuntu/.nddevice/latest/` — these are OTA-delivered and read-only
- Do NOT modify or delete `bagheera_config.ini` (under latest or bootstrap) unless explicitly specified
- Do NOT delete any certificates under `/home/ubuntu/.nddevice/certificate/` or `/home/ubuntu/backup/` (ED25519 keys, PEM files)
- Do NOT delete or modify any `.md` or `.py` files related to a service
- Do NOT change file permissions of any file unless explicitly specified
- Override configs go to `/home/ubuntu/config/bagheera_override.ini`
- The `latest` symlink must not be changed — it is managed by the OTA installer
- D450 does **NOT** support ADB — all commands go through `device_shell()` which uses `SerialTransport` internally. Never use `adb shell`, `adb push`, `adb pull`, or any ADB command
- The path `/data/nd_files/` does **NOT** exist on D450 — all data resides under `/home/ubuntu/.nddevice/` and `/media/data/`
- Do NOT delete or modify backup key files (`ed25519key.pem_*`, `pub-ed25519.pem_*`) under `/home/ubuntu/backup/`
- Do NOT modify `nddevice.ini` upgrade state manually — it is managed by the OTA/installer system
- Do NOT delete or modify scripts under `/home/ubuntu/backup/` (`start_recording.sh`, `stop_recording.sh`)
- Do NOT modify cron configurations (`conf.cron`, `root_conf.cron`) under `/home/ubuntu/.nddevice/` unless explicitly required

```

```
