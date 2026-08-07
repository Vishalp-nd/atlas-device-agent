---
name: D210_Automation
description: "Use when: running device sanity or test cases on a D210 (krait) device. Provides all config INI paths, log paths, DB paths, service lists, partition layout, and cli_mgr hardware command reference so the serial-testcase-executor agent can resolve file locations without guessing."
argument-hint: "device serial (e.g., /D210_Automation bee5935)"
---

# D210 Automation — Device Path Reference

Complete path reference for the **D210** product line (`device_type=krait`). Use this skill to resolve file locations when running generic test cases against a D210 device.

## Device Identification

| Property | Value |
|----------|-------|
| Device Type | `krait` |
| Product Line | D210 |
| OS | Linux (Qualcomm QCS605, systemd) |
| Manifest Path | `/home/ubuntu/.nddevice/latest/package_manifest.ini` |
| Device Config | `/home/ubuntu/config/deviceconfig.ini` |

Detect device type:
```bash
adb -s <SERIAL> shell "grep -i device_type /home/ubuntu/.nddevice/latest/package_manifest.ini"
# → device_type=krait
```

Read device identity (deviceId, sessionId, deviceSubType):
```bash
adb -s <SERIAL> shell "cat /home/ubuntu/config/deviceconfig.ini"
```

## Config INI Paths

### Active Config (read-only — shipped with OTA)

The `latest` symlink points to the currently active OTA version directory.

| File | Path |
|------|------|
| Package Manifest | `/home/ubuntu/.nddevice/latest/package_manifest.ini` |
| Bagheera Config | `/home/ubuntu/.nddevice/latest/bagheera_config.ini` |
| ND Config (locale-specific) | `/home/ubuntu/.nddevice/latest/nd_config.ini` |
| ND Config US | `/home/ubuntu/.nddevice/latest/nd_config_US.ini` |
| ND Config CA | `/home/ubuntu/.nddevice/latest/nd_config_CA.ini` |
| ND Config MX | `/home/ubuntu/.nddevice/latest/nd_config_MX.ini` |
| ND Config Recovery | `/home/ubuntu/.nddevice/latest/nd_config_recovery.ini` |
| ND Core Common | `/home/ubuntu/.nddevice/latest/nd_core_common.ini` |
| Cloud Config | `/home/ubuntu/.nddevice/latest/cloudconfig.ini` |
| SAM Config | `/home/ubuntu/.nddevice/latest/sam_config.ini` |
| Automation Config (OTA copy) | `/home/ubuntu/.nddevice/latest/automation_config.ini` |

> **Note:** `latest` is a symlink → resolves to `/home/ubuntu/.nddevice/<OTA_VERSION>/` (e.g., `2.6.14.rc.4`)

### Override / Runtime Config (writable)

| File | Path |
|------|------|
| Bagheera Override | `/data/nd_files/config/bagheera_override.ini` |
| Log Config | `/data/nd_files/config/logconfig.ini` |
| Device Config | `/home/ubuntu/config/deviceconfig.ini` |
| Automation Config | `/home/ubuntu/config/automation_config.ini` |
| Camera Override | `/data/nd_files/config/cam_override.ini` |
| MDVR Config | `/data/nd_files/config/mdvr_config.ini` |
| Inward Video Calibration | `/home/ubuntu/config/inward_video_pipeline_calibration.ini` |
| OBD Config | `/data/nd_files/config/obd_config.ini` |
| OBD Config Backup | `/data/nd_files/config/obd_config_backup.ini` |
| VBUS Common Config | `/data/nd_files/config/vbus_common_config.ini` |
| VBUS FW Version | `/data/nd_files/config/vbus_fw_version.ini` |
| Connection Manager Config | `/home/ubuntu/config/conn_mgr_config.txt` |

### Device State Config

| File | Path |
|------|------|
| NDDevice State (version, upgrade) | `/home/ubuntu/.nddevice/nddevice.ini` |
| Accessory Details | `/home/ubuntu/.nddevice/accessory_details.ini` |

### Bootstrap Config (factory defaults)

| File | Path |
|------|------|
| Bootstrap Cloud Config | `/home/ubuntu/.nddevice/bootstrap/cloudconfig.ini` |

### Backup Config

| File | Path |
|------|------|
| Backup directory | `/home/ubuntu/backup/` |

Contains backup copies of: `bagheera_config.ini`, `cloudconfig.ini`, `deviceconfig.ini`, `nddevice.ini`, `nd_config_recovery.ini`, `nd_config_US.ini`, `nd_config_CA.ini`, `nd_config_MX.ini`, `nd_config.ini`

## Log Paths

### Base Log Directory

```
/data/nd_files/log/
```

### Service Log Subdirectories

Each service writes logs to its own subdirectory. The `_c` suffix directories contain compressed/rotated logs.

| Service | Active Logs | Compressed Logs |
|---------|-------------|-----------------|
| analytics | `/data/nd_files/log/analytics/` | `/data/nd_files/log/analytics_c/` |
| apm | `/data/nd_files/log/apm/` | `/data/nd_files/log/apm_c/` |
| audio | `/data/nd_files/log/audio/` | `/data/nd_files/log/audio_c/` |
| awsiot | `/data/nd_files/log/awsiot/` | `/data/nd_files/log/awsiot_c/` |
| btfv | `/data/nd_files/log/btfv/` | `/data/nd_files/log/btfv_c/` |
| canAnalyticsClient | `/data/nd_files/log/canAnalyticsClient/` | `/data/nd_files/log/canAnalyticsClient_c/` |
| circ_buff | `/data/nd_files/log/circ_buff/` | `/data/nd_files/log/circ_buff_c/` |
| conn_mgr | `/data/nd_files/log/conn_mgr/` | `/data/nd_files/log/conn_mgr_c/` |
| conn_mgr_debug | `/data/nd_files/log/conn_mgr_debug/` | — |
| deleter | `/data/nd_files/log/deleter/` | `/data/nd_files/log/deleter_c/` |
| deviceHealthClient | `/data/nd_files/log/deviceHealthClient/` | `/data/nd_files/log/deviceHealthClient_c/` |
| diagnostic | `/data/nd_files/log/diagnostic/` | `/data/nd_files/log/diagnostic_c/` |
| ext_cam | `/data/nd_files/log/ext_cam/` | `/data/nd_files/log/ext_cam_c/` |
| fan | `/data/nd_files/log/fan/` | `/data/nd_files/log/fan_c/` |
| free_cache | `/data/nd_files/log/free_cache/` | — |
| fw_update | `/data/nd_files/log/fw_update/` | `/data/nd_files/log/fw_update_c/` |
| geofenceAnalyticsClient | `/data/nd_files/log/geofenceAnalyticsClient/` | `/data/nd_files/log/geofenceAnalyticsClient_c/` |
| gps | `/data/nd_files/log/gps/` | `/data/nd_files/log/gps_c/` |
| health | `/data/nd_files/log/health/` | `/data/nd_files/log/health_c/` |
| inertialAnalyticsClient | `/data/nd_files/log/inertialAnalyticsClient/` | `/data/nd_files/log/inertialAnalyticsClient_c/` |
| inference | `/data/nd_files/log/inference/` | `/data/nd_files/log/inference_c/` |
| inference_inertial | `/data/nd_files/log/inference_inertial/` | `/data/nd_files/log/inference_inertial_c/` |
| installer_app | `/data/nd_files/log/installer_app/` | `/data/nd_files/log/installer_app_c/` |
| inwardAnalyticsClient | `/data/nd_files/log/inwardAnalyticsClient/` | `/data/nd_files/log/inwardAnalyticsClient_c/` |
| keep_alive_command | `/data/nd_files/log/keep_alive_command/` | `/data/nd_files/log/keep_alive_command_c/` |
| keep_alive_manager | `/data/nd_files/log/keep_alive_manager/` | `/data/nd_files/log/keep_alive_manager_c/` |
| nd_bt_cli | `/data/nd_files/log/nd_bt_cli/` | `/data/nd_files/log/nd_bt_cli_c/` |
| nd_dta | `/data/nd_files/log/nd_dta/` | `/data/nd_files/log/nd_dta_c/` |
| nd_sam | `/data/nd_files/log/nd_sam/` | `/data/nd_files/log/nd_sam_c/` |
| nd_sam_cli | `/data/nd_files/log/nd_sam_cli/` | `/data/nd_files/log/nd_sam_cli_c/` |
| ndcentral | `/data/nd_files/log/ndcentral/` | `/data/nd_files/log/ndcentral_c/` |
| obd | `/data/nd_files/log/obd/` | `/data/nd_files/log/obd_c/` |
| otacheck | `/data/nd_files/log/otacheck/` | `/data/nd_files/log/otacheck_c/` |
| outwardAnalyticsClient | `/data/nd_files/log/outwardAnalyticsClient/` | `/data/nd_files/log/outwardAnalyticsClient_c/` |
| overspeedClient | `/data/nd_files/log/overspeedClient/` | `/data/nd_files/log/overspeedClient_c/` |
| power_mon | `/data/nd_files/log/power_mon/` | `/data/nd_files/log/power_mon_c/` |
| reboot | `/data/nd_files/log/reboot/` | `/data/nd_files/log/reboot_c/` |
| scheduler | `/data/nd_files/log/scheduler/` | `/data/nd_files/log/scheduler_c/` |
| scheduler_manager | `/data/nd_files/log/scheduler_manager/` | `/data/nd_files/log/scheduler_manager_c/` |
| service_mon | `/data/nd_files/log/service_mon/` | `/data/nd_files/log/service_mon_c/` |
| speed | `/data/nd_files/log/speed/` | `/data/nd_files/log/speed_c/` |
| svc | `/data/nd_files/log/svc/` | `/data/nd_files/log/svc_c/` |
| time_sync | `/data/nd_files/log/time_sync/` | `/data/nd_files/log/time_sync_c/` |
| unifiedAnalyticsClient | `/data/nd_files/log/unifiedAnalyticsClient/` | `/data/nd_files/log/unifiedAnalyticsClient_c/` |
| unifieduploader | `/data/nd_files/log/unifieduploader/` | `/data/nd_files/log/unifieduploader_c/` |
| updater | `/data/nd_files/log/updater/` | `/data/nd_files/log/updater_c/` |
| uploader | `/data/nd_files/log/uploader/` | `/data/nd_files/log/uploader_c/` |
| wifi_mgr | `/data/nd_files/log/wifi_mgr/` | `/data/nd_files/log/wifi_mgr_c/` |

### IMPORTANT: Log File Naming & How to Read Logs

Log files inside service subdirectories do **NOT** have predictable names like `ndcentral.log` or `power_mon.log`. They use **timestamped names**, e.g.:

```
log_20260511_120000.log
log_20260511_143022.log
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

| File | Path |
|------|------|
| Health Stats Log | `/data/nd_files/log/healthstats.log` |
| Health Stats Upload Response | `/data/nd_files/log/healthstatsUploadResponse.log` |
| NDC Common Log | `/data/nd_files/log/ndc_common.log` |
| Timer Log | `/data/nd_files/log/timer.log` |
| Timer2 Log | `/data/nd_files/log/timer2.log` |
| Wrapper CleanupState Log | `/data/nd_files/log/wrapper_cleanupstate.log` |
| Wrapper OTACheck Log | `/data/nd_files/log/wrapper_otacheck.log` |
| Wrapper Scheduler Log | `/data/nd_files/log/wrapper_scheduler.log` |
| Service Monitor Events | `/data/nd_files/log/sm_critical_events.json` |
| Automation System Log | `/data/nd_files/log/automationsys.log` |
| GPS Restart Log | `/data/nd_files/log/gps_restart.log` |
| OBD Property Copy Log | `/data/nd_files/log/obd_property_copy.log` |
| ISP Flash Log | `/data/nd_files/log/isp_flash.log` |

### Log Archive & Upload

| Directory | Path |
|-----------|------|
| Log Archive | `/data/nd_files/log/archive/` |
| Logs Upload Staging | `/data/nd_files/logsUpload/` |

## Database Paths

All databases are SQLite and located under `/data/nd_files/db/`.

| Database | Path | Purpose |
|----------|------|---------|
| accessory.db | `/data/nd_files/db/accessory.db` | Accessory/peripheral tracking |
| camera_crash.db | `/data/nd_files/db/camera_crash.db` | Camera crash events |
| circular_buffer.db | `/data/nd_files/db/circular_buffer.db` | Circular buffer metadata |
| diagnostic.db | `/data/nd_files/db/diagnostic.db` | Diagnostic data |
| ea.db | `/data/nd_files/db/ea.db` | Event analytics |
| eld.db | `/data/nd_files/db/eld.db` | Electronic logging device |
| emmc_health.db | `/data/nd_files/db/emmc_health.db` | eMMC health monitoring |
| emmc_health_backup.db | `/data/nd_files/db/emmc_health_backup.db` | eMMC health backup |
| ext_cam_config.db | `/data/nd_files/db/ext_cam_config.db` | External camera configuration |
| gen_property.db | `/data/nd_files/db/gen_property.db` | General device properties |
| healthstats.db | `/data/nd_files/db/healthstats.db` | Health statistics |
| iosix_gps.db | `/data/nd_files/db/iosix_gps.db` | IOSiX GPS data |
| keep_alive_mgr.db | `/data/nd_files/db/keep_alive_mgr.db` | Keep-alive manager state |
| login.db | `/data/nd_files/db/login.db` | Login/authentication data |
| obd_property.db | `/data/nd_files/db/obd_property.db` | OBD property data |
| power_monitor.db | `/data/nd_files/db/power_monitor.db` | Power monitoring data |
| THRESHOLD.db | `/data/nd_files/db/THRESHOLD.db` | Threshold configuration |
| HEALTH.db | `/data/nd_files/db/HEALTH.db` | Health data |
| HERE.db | `/data/nd_files/db/HERE.db` | HERE maps/location data |
| udid.db | `/data/nd_files/db/udid.db` | Unique device identifier |
| uploader.db | `/data/nd_files/db/uploader.db` | Upload queue/state |

Additional DB files under `/home/ubuntu/.nddevice/db/`:

| Database | Path |
|----------|------|
| gen_property.db | `/home/ubuntu/.nddevice/db/gen_property.db` |
| camera_crash.db | `/home/ubuntu/.nddevice/db/camera_crash.db` |
| ext_cam_config.db | `/home/ubuntu/.nddevice/db/ext_cam_config.db` |

Additional files under `/home/ubuntu/.nddevice/`:

| Database | Path |
|----------|------|
| BT ADSM State | `/home/ubuntu/.nddevice/bt_adsm_state.db` |

## Data Partition Layout

| Directory | Path | Purpose |
|-----------|------|---------|
| Config | `/data/nd_files/config/` | Override configs, logconfig |
| Database | `/data/nd_files/db/` | All SQLite databases |
| Logs | `/data/nd_files/log/` | All service logs |
| Log Upload | `/data/nd_files/logsUpload/` | Staged logs for upload |
| Observations | `/data/nd_files/observations/` | Analytics observations |
| Inertial Obs | `/data/nd_files/inertial_obs/` | Inertial sensor observations |
| SD Card (video) | `/data/nd_files/nd_sdcard/` | Video circular buffer storage |
| Certificates | `/data/nd_files/certificate/` | TLS/AWS certs (`cacert.pem`, `root-CA.crt`) |
| Cloud Response | `/data/nd_files/cloud_response/` | Cached cloud responses |
| Save Metadata | `/data/nd_files/saveMetadata/` | Saved metadata |
| State Files | `/data/nd_files/state_files/` | Runtime state persistence |
| MSGQ | `/data/nd_files/MSGQ/` | Message queue IPC |
| Backup | `/data/nd_files/backup/` | Config backups |
| Sign Crops | `/data/nd_files/sign_crops/` | Sign detection crops |
| Iris CLI | `/data/nd_files/iriscli/` | Iris CLI data |
| Unoperated Obs | `/data/nd_files/unoperated_obs/` | Unprocessed observations |
| Autocam | `/data/nd_files/autocam/` | Auto-camera data |
| Sierra FW | `/data/nd_files/sierra_fw/` | Sierra modem firmware |
| FW Download | `/data/nd_files/fw_download/` | Firmware download staging |
| OTA Download | `/data/nd_files/ota_download/` | OTA download staging |

### Disk Partitions

| Partition | Mount | Size | Purpose |
|-----------|-------|------|---------|
| `/dev/mmcblk0p66` | `/data` | ~106 GB | Data partition (video, logs, DB) |
| `/dev/root` | `/` | ~3 GB | Root filesystem (OS, configs, nddevice) |

> **Key difference from D470:** D210 uses a smaller root partition (~3 GB vs 28 GB) and the data partition is ~106 GB vs 200 GB.

## Services (systemd)

All ND services on D210 that must be `active (running)` for a healthy device:

```
analytics.service
apm.service
audioPlayback.service
awsiot.service
bagheera.service
circular_buffer.service
conn_mgr.service
dcam.service
diagnostic.service
fan_control.service
free_cache.service
gps.service
HealthStatsManager.service
installer_app.service
inwardAnalyticsClient.service
nd_bt.service
nd_dta.service
nd_sam.service
obd.service
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
adb -s <SERIAL> shell "systemctl is-active analytics apm audioPlayback awsiot bagheera circular_buffer conn_mgr dcam diagnostic fan_control free_cache gps HealthStatsManager installer_app inwardAnalyticsClient nd_bt nd_dta nd_sam obd outwardAnalyticsClient power_monitor scheduler_manager service_mon speed svc time_sync unifiedAnalyticsClient uploader wifi_mgr"
```

### D210-specific services (not on D470)

| Service | Purpose |
|---------|---------|
| `dcam.service` | D-camera (dual camera) service |
| `fan_control.service` | Fan control daemon |
| `free_cache.service` | Cache cleanup service |
| `obd.service` | OBD-II vehicle diagnostics |

### D470 services NOT present on D210

| Service | Notes |
|---------|-------|
| `cam_rec.service` | D210 uses `dcam.service` instead |
| `canAnalyticsClient.service` | Not present |
| `deviceHealthClient.service` | Not present |
| `dmsAnalyticsClient.service` | Not present |

## Hardware Validation — cli_mgr Reference

### What is cli_mgr on D210?

`cli_mgr` is an interactive command-line interface on D210 devices for direct hardware subsystem control.

- **Binary location:** `/usr/bin/cli_mgr`
- **Prompt:** `NTDI_DCAM >` (different from D470's `NTDI_B4 >`)
- **Entry:** run `cli_mgr` in an ADB shell
- **Exit:** send `exit` command inside cli_mgr

### Executing cli_mgr Commands via ADB

#### Single Command

```bash
adb -s <SERIAL> shell 'printf "<COMMAND>\nexit\n" | cli_mgr 2>&1'
```

#### Multiple Commands in One Session

```bash
adb -s <SERIAL> shell 'printf "<CMD1>\n<CMD2>\n<CMD3>\nexit\n" | cli_mgr 2>&1'
```

#### Important Notes

- **Always append `exit` as the last command** — otherwise cli_mgr hangs waiting for input.
- **Use `printf` with `\n`** — do NOT use `echo` (it sends all text as one line).
- **Pipe output through `2>&1`** — cli_mgr writes some output to stderr.
- **Output includes the prompt echo** — e.g., `NTDI_DCAM > temp read` appears before the result.
- **Prompt is `NTDI_DCAM >`** not `NTDI_B4 >` — test cases must match the correct prompt string.

### Available Subsystems

```
imu              - IMU Device
led              - LED
button           - Button
irled            - IRLED
version          - Displays the version (board rev, BSP, MSP FW)
cfg_lumia_rev_gpio - Configure the lumia GPIO
wdt              - Watchdog timer
modem_temperature - Reads the Modem temperature value
temp             - TEMP Sensor
msp              - MSP controller
power_disconnect - Configure power disconnect
fan              - FAN TACHOMETER
ubx-gps          - UBLOX GPS API test
```

### cli_mgr Command Reference

#### Version Command

| Command | Expected Output | Notes |
|---------|----------------|-------|
| `version` | `Board Revision:REV_D`, `BSP version:X.X.X`, `MSP: Firmware version is XX` | Returns board HW rev, BSP version, MSP FW version |

#### Temperature Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `temp read` | `TEMP102:<N> deg C`, `CPUTEMP:<N> deg C`, `IMU:Temp:<N> deg C`, `LTE:Temp:<N> deg C` | Reads all temperature sensors in one call. No init/uninit needed |
| `modem_temperature` | `LTE Modem temperature = <N> deg C` | Standalone modem temp. Returns `-1000` if Sierra modem not detected |

> **Key difference from D470:** D210 uses `temp read` (no init/uninit), D470 uses `temp init` → `temp get_temp 12` → `temp uninit`.

#### LED Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `led set <led_id> <color> <intensity>` | (no output = success) | led_id: valid range TBD; color: `R`, `G`, `B`; intensity: 0–255 |
| `led clear <led_id> <color>` | (no output = success) | Clears specified LED color |

> **Note:** `led set 1 R 255` returns "Invalid LED number" and `led set 0 R 255` returns "Error in LED Number". Valid LED IDs are device-specific — verify with hardware team.

#### IRLED Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `irled set <brightness>` | `IRLED: Set Success with brightness level <N>` | Valid brightness: 1–4 (0 and values > 4 return `IRLED_ERR:Input error`) |

> **Key difference from D470:** D210 uses `irled set <N>`, D470 uses `irled init` → `irled on/off` → `irled uninit`.

#### IMU Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `imu enable poll_mode <interval>` | IMU enable confirmation | Requires valid interval parameter |
| `imu read` | IMU sensor data (accel/gyro) | Device must be enabled first; returns "Device is Not opened" otherwise |
| `imu read_data` | IMU sensor data | Same prerequisite as `imu read` |
| `imu disable` | IMU disable confirmation | Returns "IMU Device is Not opened" if not enabled |

> **Key difference from D470:** D210 uses `imu enable`/`imu disable` pattern, D470 uses `imu init`/`imu uninit`.

#### MSP Controller Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `msp read fw` | `MSP: Firmware version is <N>` | Read MSP firmware version |
| `msp read phototransistor` | `MSP: Phototransistor value is 0x<HEX>` | Ambient light sensor reading |
| `msp read tmp112_conf` | `MSP: TMP112 CONF register is 0x<HEX>` | TMP112 temperature sensor config register |
| `msp read tmp112_thigh` | `MSP: TMP112 THIGH register value` | TMP112 high-temp threshold |
| `msp read tmp112_tlow` | `MSP: TMP112 TLOW register value` | TMP112 low-temp threshold |
| `msp write <register> <value>` | Write confirmation | Write to MSP registers (use with caution) |

#### Fan Commands

The fan subsystem on D210 uses `fan_control.service` (systemd) rather than cli_mgr commands. Fan logs are in `/data/nd_files/log/fan/`.

> **Key difference from D470:** D470 uses `fan init`/`fan on`/`fan off`/`fan get_status`/`fan uninit` in cli_mgr. D210 fan is managed by `fan_control.service`.

#### Watchdog Timer (WDT) Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `wdt stop` | WDT stop confirmation | Returns "WDT device is not opened" if not started |

> WDT open/close/keepalive are internal — the watchdog is typically managed by the system.

#### Power Disconnect Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `power_disconnect registerCB` | Callback registration | Register for power disconnect events |
| `power_disconnect unregisterCB` | Callback unregistration | Unregister callback |

#### UBX-GPS Commands

The `ubx-gps` subsystem provides UBLOX GPS API access. Commands follow the `ubx-gps get_<param>` pattern.

> GPS data is typically accessed via the `gps.service` logs at `/data/nd_files/log/gps/` rather than cli_mgr for test validation.

### D210 vs D470 cli_mgr Differences Summary

| Feature | D210 (krait) | D470 (bagheera4) |
|---------|-------------|------------------|
| Prompt | `NTDI_DCAM >` | `NTDI_B4 >` |
| Temperature | `temp read` (all-in-one, no init) | `temp init` → `temp get_temp 12` → `temp uninit` |
| Fan | `fan_control.service` (systemd) | `fan init/on/off/get_status/uninit` (cli_mgr) |
| IRLED | `irled set <brightness>` (1–4) | `irled init/on/off/uninit` |
| IMU | `imu enable/disable` | `imu init/uninit` |
| GPIO | Not available in cli_mgr | `gpio get <pin>` |
| Ignition | Not in cli_mgr (use APM logs) | `ignition status/register/unregister` |
| Supercap | Not available | `supercap init/get_status/uninit` |
| ADC | Not available | `adc init/get_value/uninit` |
| MSP | `msp read fw/phototransistor/tmp112_*` | Not available |
| UBX-GPS | `ubx-gps get_*` | Not available |
| Modem Temp | `modem_temperature` | Not available |

## How the serial-testcase-executor Agent Should Use This

When running a generic test case against a D210 device:

1. **Detect device type** — read `package_manifest.ini` and confirm `device_type=krait`
2. **Resolve paths** — use this skill's tables to map generic references (e.g., "bagheera config", "override config", "log directory") to the exact D210 paths
3. **Config checks** — use the **Active Config** paths for read-only validation, **Override Config** paths for writable config checks
4. **Log checks** — use `/data/nd_files/log/<service>/` for active logs
5. **DB checks** — use `/data/nd_files/db/<name>.db` for database validation
6. **Service checks** — validate against the D210 service list above (not the D470 lists). Note D210-specific services like `dcam`, `fan_control`, `free_cache`, `obd`
7. **Disk checks** — `/data` partition (~106 GB) for data, root partition (~3 GB) for OS
8. **Hardware validation** — use cli_mgr with `NTDI_DCAM >` prompt and D210-specific command syntax (no init/uninit for temp, irled uses `set <N>`, etc.)
9. **Shutdown/power checks** — for krait, look for `shutdown_log` in power_mon logs (not `Calling command: shutdown now` which is bagheera-specific)

## Constraints

- Do NOT modify files under `/home/ubuntu/.nddevice/latest/` — these are OTA-delivered and read-only
- Override configs go to `/data/nd_files/config/bagheera_override.ini`
- Use the `config-override` skill for modifying override config
- Use the `relay-control` skill for ignition relay operations
- The `latest` symlink must not be changed — it is managed by the OTA installer
- Sierra modem may not be detected on all D210 units — modem-dependent commands will fail gracefully
