---
name: D470_Automation
description: "Use when: running device sanity or test cases on a D470 (bagheera4) device. Provides all config INI paths, log paths, DB paths, service lists, and partition layout so the serial-testcase-executor agent can resolve file locations without guessing."
argument-hint: "device serial (e.g., /D470_Automation 2543fa04)"
---
# D470 Automation — Device Path Reference

Complete path reference for the **D470** product line (`device_type=bagheera4`). Use this skill to resolve file locations when running generic test cases against a D470 device.

## Device Identification

| Property      | Value                                                  |
| ------------- | ------------------------------------------------------ |
| Device Type   | `bagheera4`                                          |
| Product Line  | D470                                                   |
| OS            | Ubuntu Linux (systemd)                                 |
| Manifest Path | `/home/ubuntu/.nddevice/latest/package_manifest.ini` |
| Device Config | `/home/ubuntu/config/deviceconfig.ini`               |

Detect device type:

```bash
adb -s <SERIAL> shell "grep -i device_type /home/ubuntu/.nddevice/latest/package_manifest.ini"
# → device_type=bagheera4
```

Read device identity (deviceId, sessionId, deviceSubType):

```bash
adb -s <SERIAL> shell "cat /home/ubuntu/config/deviceconfig.ini"
```

## Config INI Paths

### Active Config (read-only — shipped with OTA)

The `latest` symlink points to the currently active OTA version directory.

| File                         | Path                                                               |
| ---------------------------- | ------------------------------------------------------------------ |
| Package Manifest             | `/home/ubuntu/.nddevice/latest/package_manifest.ini`             |
| Bagheera Config              | `/home/ubuntu/.nddevice/latest/bagheera_config.ini`              |
| ND Config (locale-specific)  | `/home/ubuntu/.nddevice/latest/nd_config.ini`                    |
| ND Config US                 | `/home/ubuntu/.nddevice/latest/nd_config_US.ini`                 |
| ND Config CA                 | `/home/ubuntu/.nddevice/latest/nd_config_CA.ini`                 |
| ND Config MX                 | `/home/ubuntu/.nddevice/latest/nd_config_MX.ini`                 |
| ND Config Recovery           | `/home/ubuntu/.nddevice/latest/nd_config_recovery.ini`           |
| ND Core Common               | `/home/ubuntu/.nddevice/latest/nd_core_common.ini`               |
| Cloud Config                 | `/home/ubuntu/.nddevice/latest/cloudconfig.ini`                  |
| SAM Config                   | `/home/ubuntu/.nddevice/latest/sam_config.ini`                   |
| Automation Config (OTA copy) | `/home/ubuntu/.nddevice/latest/automation_config.ini`            |
| DMS Installer Config         | `/home/ubuntu/.nddevice/latest/nd_dms_installer_config_snpe.ini` |

> **Note:** `latest` is a symlink → resolves to `/home/ubuntu/.nddevice/<OTA_VERSION>/`

### Override / Runtime Config (writable)

| File                     | Path                                                          |
| ------------------------ | ------------------------------------------------------------- |
| Bagheera Override        | `/data/nd_files/config/bagheera_override.ini`               |
| Log Config               | `/data/nd_files/config/logconfig.ini`                       |
| Device Config            | `/home/ubuntu/config/deviceconfig.ini`                      |
| Automation Config        | `/home/ubuntu/config/automation_config.ini`                 |
| Camera Override          | `/home/ubuntu/config/cam_override.ini`                      |
| MDVR Config              | `/home/ubuntu/config/mdvr_config.ini`                       |
| Inward Video Calibration | `/home/ubuntu/config/inward_video_pipeline_calibration.ini` |

### Device State Config

| File                              | Path                                       |
| --------------------------------- | ------------------------------------------ |
| NDDevice State (version, upgrade) | `/home/ubuntu/.nddevice/nddevice.ini`    |
| MDVR Config                       | `/home/ubuntu/.nddevice/mdvr_config.ini` |

### Bootstrap Config (factory defaults)

| File                      | Path                                                     |
| ------------------------- | -------------------------------------------------------- |
| Bootstrap Bagheera Config | `/home/ubuntu/.nddevice/bootstrap/bagheera_config.ini` |
| Bootstrap Cloud Config    | `/home/ubuntu/.nddevice/bootstrap/cloudconfig.ini`     |

### Backup Config

| File             | Path                     |
| ---------------- | ------------------------ |
| Backup directory | `/home/ubuntu/backup/` |

Contains backup copies of: `bagheera_config.ini`, `cloudconfig.ini`, `deviceconfig.ini`, `nddevice.ini`, `nd_config_recovery.ini`, `nd_config_US.ini`, `nd_config_CA.ini`, `nd_config_MX.ini`

## Log Paths

### Base Log Directory

```
/data/nd_files/log/
```

### Service Log Subdirectories

Each service writes logs to its own subdirectory, .log files for the respective services are present under the respective sub directory which need to be used for searching log patterns. The `_c` suffix directories contain critical logs.

| Service                 | Active Logs                                     | Critical Logs                                    |
| ----------------------- | ----------------------------------------------- | ------------------------------------------------- |
| analytics               | `/data/nd_files/log/analytics/`               | `/data/nd_files/log/analytics_c/`               |
| apm                     | `/data/nd_files/log/apm/`                     | `/data/nd_files/log/apm_c/`                     |
| audio                   | `/data/nd_files/log/audio/`                   | `/data/nd_files/log/audio_c/`                   |
| awsiot                  | `/data/nd_files/log/awsiot/`                  | —                                                |
| btfv                    | `/data/nd_files/log/btfv/`                    | `/data/nd_files/log/btfv_c/`                    |
| cam_rec                 | `/data/nd_files/log/cam_rec/`                 | `/data/nd_files/log/cam_rec_c/`                 |
| canAnalyticsClient      | `/data/nd_files/log/canAnalyticsClient/`      | `/data/nd_files/log/canAnalyticsClient_c/`      |
| circ_buff               | `/data/nd_files/log/circ_buff/`               | `/data/nd_files/log/circ_buff_c/`               |
| conn_mgr                | `/data/nd_files/log/conn_mgr/`                | `/data/nd_files/log/conn_mgr_c/`                |
| deleter                 | `/data/nd_files/log/deleter/`                 | `/data/nd_files/log/deleter_c/`                 |
| deviceHealthClient      | `/data/nd_files/log/deviceHealthClient/`      | `/data/nd_files/log/deviceHealthClient_c/`      |
| diagnostic              | `/data/nd_files/log/diagnostic/`              | `/data/nd_files/log/diagnostic_c/`              |
| dmsAnalyticsClient      | `/data/nd_files/log/dmsAnalyticsClient/`      | `/data/nd_files/log/dmsAnalyticsClient_c/`      |
| ext_cam                 | `/data/nd_files/log/ext_cam/`                 | `/data/nd_files/log/ext_cam_c/`                 |
| gps                     | `/data/nd_files/log/gps/`                     | `/data/nd_files/log/gps_c/`                     |
| health                  | `/data/nd_files/log/health/`                  | `/data/nd_files/log/health_c/`                  |
| inertialAnalyticsClient | `/data/nd_files/log/inertialAnalyticsClient/` | `/data/nd_files/log/inertialAnalyticsClient_c/` |
| inference               | `/data/nd_files/log/inference/`               | `/data/nd_files/log/inference_c/`               |
| inference_inertial      | `/data/nd_files/log/inference_inertial/`      | `/data/nd_files/log/inference_inertial_c/`      |
| installer_app           | `/data/nd_files/log/installer_app/`           | `/data/nd_files/log/installer_app_c/`           |
| inwardAnalyticsClient   | `/data/nd_files/log/inwardAnalyticsClient/`   | `/data/nd_files/log/inwardAnalyticsClient_c/`   |
| keep_alive_manager      | `/data/nd_files/log/keep_alive_manager/`      | `/data/nd_files/log/keep_alive_manager_c/`      |
| nd_app_reboot           | `/data/nd_files/log/nd_app_reboot/`           | `/data/nd_files/log/nd_app_reboot_c/`           |
| nd_dta                  | `/data/nd_files/log/nd_dta/`                  | `/data/nd_files/log/nd_dta_c/`                  |
| nd_sam                  | `/data/nd_files/log/nd_sam/`                  | `/data/nd_files/log/nd_sam_c/`                  |
| ndcentral               | `/data/nd_files/log/ndcentral/`               | `/data/nd_files/log/ndcentral_c/`               |
| otacheck                | `/data/nd_files/log/otacheck/`                | `/data/nd_files/log/otacheck_c/`                |
| outwardAnalyticsClient  | `/data/nd_files/log/outwardAnalyticsClient/`  | `/data/nd_files/log/outwardAnalyticsClient_c/`  |
| overspeedClient         | `/data/nd_files/log/overspeedClient/`         | `/data/nd_files/log/overspeedClient_c/`         |
| power_mon               | `/data/nd_files/log/power_mon/`               | `/data/nd_files/log/power_mon_c/`               |
| reboot                  | `/data/nd_files/log/reboot/`                  | `/data/nd_files/log/reboot_c/`                  |
| scheduler               | `/data/nd_files/log/scheduler/`               | `/data/nd_files/log/scheduler_c/`               |
| scheduler_manager       | `/data/nd_files/log/scheduler_manager/`       | `/data/nd_files/log/scheduler_manager_c/`       |
| service_mon             | `/data/nd_files/log/service_mon/`             | `/data/nd_files/log/service_mon_c/`             |
| speed                   | `/data/nd_files/log/speed/`                   | `/data/nd_files/log/speed_c/`                   |
| svc                     | `/data/nd_files/log/svc/`                     | `/data/nd_files/log/svc_c/`                     |
| time_sync               | `/data/nd_files/log/time_sync/`               | `/data/nd_files/log/time_sync_c/`               |
| unifiedAnalyticsClient  | `/data/nd_files/log/unifiedAnalyticsClient/`  | `/data/nd_files/log/unifiedAnalyticsClient_c/`  |
| unifieduploader         | `/data/nd_files/log/unifieduploader/`         | `/data/nd_files/log/unifieduploader_c/`         |
| uploader                | `/data/nd_files/log/uploader/`                | `/data/nd_files/log/uploader_c/`                |
| wifi_mgr                | `/data/nd_files/log/wifi_mgr/`                | `/data/nd_files/log/wifi_mgr_c/`                |

### IMPORTANT: Log File Naming & How to Read Logs

Log files inside service subdirectories do **NOT** have predictable names like `ndcentral.log` or `power_mon.log`. They use **timestamped names**, e.g.:

```
log_1778491799000.log
log_1778489968000.log
```

**Rules for reading logs:**

- **To search for a pattern** → use the `grep_logs(service, pattern)` tool. It automatically finds recent files and greps them. Do NOT manually grep with `device_shell`.
- **To read recent log output** → use `tail` on all files, sorted by modification time:
  ```bash
  device_shell("ls -t /data/nd_files/log/<service>/ | head -1 | xargs -I{} tail -n 100 /data/nd_files/log/<service>/{}")
  ```
- **NEVER** guess a filename like `ndcentral.log`, `bagheera.log`, `power_mon.log`, etc. — these files do not exist.
- **NEVER** use `cat /data/nd_files/log/<service>/<service>.log` — it will fail.

### Standalone Log Files

| File                         | Path                                                 |
| ---------------------------- | ---------------------------------------------------- |
| Health Stats Log             | `/data/nd_files/log/healthstats.log`               |
| Health Stats Upload Response | `/data/nd_files/log/healthstatsUploadResponse.log` |
| NDC Common Log               | `/data/nd_files/log/ndc_common.log`                |
| Timer Log                    | `/data/nd_files/log/timer.log`                     |
| Timer2 Log                   | `/data/nd_files/log/timer2.log`                    |
| Wrapper CleanupState Log     | `/data/nd_files/log/wrapper_cleanupstate.log`      |
| Wrapper OTACheck Log         | `/data/nd_files/log/wrapper_otacheck.log`          |
| Wrapper Scheduler Log        | `/data/nd_files/log/wrapper_scheduler.log`         |
| Service Monitor Events       | `/data/nd_files/log/sm_critical_events.json`       |

### Log Archive & Upload

| Directory           | Path                            |
| ------------------- | ------------------------------- |
| Log Archive         | `/data/nd_files/log/archive/` |
| Logs Upload Staging | `/data/nd_files/logsUpload/`  |

## Database Paths

All databases are SQLite and located under `/data/nd_files/db/`.

| Database              | Path                                        | Purpose                       |
| --------------------- | ------------------------------------------- | ----------------------------- |
| accessory.db          | `/data/nd_files/db/accessory.db`          | Accessory/peripheral tracking |
| camera_crash.db       | `/data/nd_files/db/camera_crash.db`       | Camera crash events           |
| circular_buffer.db    | `/data/nd_files/db/circular_buffer.db`    | Circular buffer metadata      |
| ea.db                 | `/data/nd_files/db/ea.db`                 | Event analytics               |
| emmc_health.db        | `/data/nd_files/db/emmc_health.db`        | eMMC health monitoring        |
| emmc_health_backup.db | `/data/nd_files/db/emmc_health_backup.db` | eMMC health backup            |
| ext_cam_config.db     | `/data/nd_files/db/ext_cam_config.db`     | External camera configuration |
| gen_property.db       | `/data/nd_files/db/gen_property.db`       | General device properties     |
| healthstats.db        | `/data/nd_files/db/healthstats.db`        | Health statistics             |
| keep_alive_mgr.db     | `/data/nd_files/db/keep_alive_mgr.db`     | Keep-alive manager state      |
| power_monitor.db      | `/data/nd_files/db/power_monitor.db`      | Power monitoring data         |
| THRESHOLD.db          | `/data/nd_files/db/THRESHOLD.db`          | Threshold configuration       |
| HEALTH.db             | `/data/nd_files/db/HEALTH.db`             | Health data                   |
| udid.db               | `/data/nd_files/db/udid.db`               | Unique device identifier      |
| uploader.db           | `/data/nd_files/db/uploader.db`           | Upload queue/state            |

Additional DB files under `/home/ubuntu/.nddevice/`:

| Database          | Path                                         |
| ----------------- | -------------------------------------------- |
| gen_property.db   | `/home/ubuntu/.nddevice/gen_property.db`   |
| camera_crash.db   | `/home/ubuntu/.nddevice/camera_crash.db`   |
| ext_cam_config.db | `/home/ubuntu/.nddevice/ext_cam_config.db` |
| BLE login.db      | `/home/ubuntu/.nddevice/.ble/login.db`     |

## Data Partition Layout

| Directory       | Path                               | Purpose                                         |
| --------------- | ---------------------------------- | ----------------------------------------------- |
| Config          | `/data/nd_files/config/`         | Override configs, logconfig                     |
| Database        | `/data/nd_files/db/`             | All SQLite databases                            |
| Logs            | `/data/nd_files/log/`            | All service logs                                |
| Log Upload      | `/data/nd_files/logsUpload/`     | Staged logs for upload                          |
| Observations    | `/data/nd_files/observations/`   | Analytics observations                          |
| Inertial Obs    | `/data/nd_files/inertial_obs/`   | Inertial sensor observations                    |
| SD Card (video) | `/data/nd_files/nd_sdcard/`      | Video circular buffer storage                   |
| Certificates    | `/data/nd_files/certificate/`    | TLS/AWS certs (`cacert.pem`, `root-CA.crt`) |
| Cloud Response  | `/data/nd_files/cloud_response/` | Cached cloud responses                          |
| Save Metadata   | `/data/nd_files/saveMetadata/`   | Saved metadata                                  |
| State Files     | `/data/nd_files/state_files/`    | Runtime state persistence                       |
| MSGQ            | `/data/nd_files/MSGQ/`           | Message queue IPC                               |
| Backup          | `/data/nd_files/backup/`         | Config backups                                  |
| Sign Crops      | `/data/nd_files/sign_crops/`     | Sign detection crops                            |
| Iris CLI        | `/data/nd_files/iriscli/`        | Iris CLI data                                   |
| Unoperated Obs  | `/data/nd_files/unoperated_obs/` | Unprocessed observations                        |

### Disk Partitions

| Partition           | Mount                   | Size   | Purpose                          |
| ------------------- | ----------------------- | ------ | -------------------------------- |
| `/dev/mmcblk0p70` | `/data`               | 200 GB | Data partition (video, logs, DB) |
| `/dev/mmcblk0p71` | `/var` (→ `/home`) | 28 GB  | OS, configs, nddevice            |

## Services (systemd)

All ND services on D470 that must be `active (running)` for a healthy device:

```
analytics.service
apm.service
audioPlayback.service
awsiot.service
bagheera.service
cam_rec.service
canAnalyticsClient.service
circular_buffer.service
conn_mgr.service
deviceHealthClient.service
diagnostic.service
dmsAnalyticsClient.service
gps.service
HealthStatsManager.service
installer_app.service
inwardAnalyticsClient.service
nd_bt.service
nd_dta.service
nd_sam.service
outwardAnalyticsClient.service
power_monitor.service
scheduler_manager.service
service_mon.service
speed.service
svc.service
time_sync.service
unifiedAnalyticsClient.service
uploader.service
wifi_mgr.service
```

Check all services:

```bash
adb -s <SERIAL> shell "systemctl is-active analytics apm audioPlayback awsiot bagheera cam_rec canAnalyticsClient circular_buffer conn_mgr deviceHealthClient diagnostic dmsAnalyticsClient gps HealthStatsManager installer_app inwardAnalyticsClient nd_bt nd_dta nd_sam outwardAnalyticsClient power_monitor scheduler_manager service_mon speed svc time_sync unifiedAnalyticsClient uploader wifi_mgr"
```

## How the serial-testcase-executor Agent Should Use This

When running a generic test case against a D470 device:

1. **Detect device type** — read `package_manifest.ini` and confirm `device_type=bagheera4`
2. **Resolve paths** — use this skill's tables to map generic references (e.g., "bagheera config", "override config", "log directory") to the exact D470 paths
3. **Config checks** — use the **Active Config** paths for read-only validation, **Override Config** paths for writable config checks
4. **Log checks** — use `/data/nd_files/log/<service>/` for active logs
5. **DB checks** — use `/data/nd_files/db/<name>.db` for database validation
6. **Service checks** — validate against the D470 service list above (not the bagheera/krait lists)
7. **Disk checks** — `/data` partition (200 GB) for data, `/var` partition (28 GB) for OS/home

## Constraints

- Do NOT modify files under `/home/ubuntu/.nddevice/latest/` — these are OTA-delivered and read-only
- Override configs go to `/data/nd_files/config/bagheera_override.ini`
- Use the `config-override` skill for modifying override config
- Use the `relay-control` skill for ignition relay operations
- The `latest` symlink must not be changed — it is managed by the OTA installer
