---
name: gps-service-validation
description: "Use when: running GPS service validation test cases on Netradyne devices. Covers service initialization and status, config parsing, NMEA log file creation/rotation, GPS data publishing via AWSIOT every 10 seconds, socket binding, port detection and initialization, first fix and TTFF timing, session creation, dual livestreaming behavior, HD maps PPS data, user alert flow when GPS inactive, and speed monitoring dependency."
argument-hint: "device serial (e.g., /gps-service-validation 2543fa04)"
---

# GPS Service Validation — Test Reference

This skill enables the **serial-testcase-executor** agent to run GPS service validation test cases on Netradyne devices (krait, krait2, bagheera2, bagheera3, octo).

## What is the GPS Service?

`gps` is a system service that:
- **Reads GPS data** — Monitors GPS hardware for position, speed, and satellite data every 2 seconds.
- **Publishes GPS data** — Sends GPS data to AWSIOT every 10 seconds for cloud consumption.
- **NMEA logging** — Optionally logs raw NMEA data when `nmea_log_enable = true`.
- **Log rotation** — Rotates GPS log files at configured intervals; logs are zipped and uploaded by KAM.
- **Socket binding** — Binds to a socket for inter-process communication with other services.
- **Port detection** — Detects, initializes, and connects to GPS hardware ports.
- **First fix** — Reports Time To First Fix (TTFF) when GPS acquires satellite lock.
- **Session management** — Creates GPS sessions; handles GPS unavailability gracefully.
- **Speed integration** — Speed service monitors GPS-based speed every 5 seconds.
- **Dual livestreaming** — Supports dual livestreaming with GPS data when active/inactive.
- **HD Maps** — Provides PPS (Pulse Per Second) data when HD maps are enabled.
- **User alerts** — Triggers user alert flow when GPS is inactive.

**Service name:** `gps`
**Log folder:** `/home/ubuntu/.nddevice/log/gps/`
**Config file:** `bagheera_config.ini` and `bagheera_override.ini`

---

## GPS Configuration Parameters

| Config Key | Section | Default | Description |
|-----------|---------|---------|-------------|
| `nmea_log_enable` | `[gps]` | `false` | Enable/disable NMEA raw data logging |
| `log_interval` | `[gps]` | varies | GPS log rotation interval |
| `gps_data_interval` | `[gps]` | `2` | GPS data monitoring interval in seconds |
| `publish_interval` | `[awsiot]` | `10` | GPS data publishing interval to AWSIOT |
| `hdmaps_enable` | `[gps]` | `false` | Enable HD maps PPS data |

---

## Key Log Patterns

| Pattern | Meaning |
|---------|---------|
| `GPS service is active` | Service running and healthy |
| `GPS data monitored` | GPS data read cycle completed |
| `NMEA log enabled` / `NMEA log disabled` | NMEA logging state |
| `GPS log file created` | New log file created |
| `GPS log rotated` | Log file rotation completed |
| `GPS log zipped` | Log file compressed for upload |
| `Socket bound successfully` | IPC socket initialized |
| `Timer initialized` | GPS data timer started |
| `Port detected` | GPS hardware port found |
| `Port initialized` | GPS port opened and configured |
| `Port connected` | GPS communication established |
| `First fix acquired` | GPS satellite lock obtained |
| `TTFF: <ms>` | Time To First Fix in milliseconds |
| `GPS session created` | GPS session started |
| `GPS not available` | GPS hardware not detected |
| `GPS data published to AWSIOT` | GPS data sent to cloud |
| `Speed monitoring GPS every 5 secs` | Speed service reading GPS data |
| `Dual livestreaming active` | Both cameras streaming with GPS |
| `PPS data available` | Pulse Per Second data for HD maps |
| `User alert: GPS inactive` | Alert triggered for GPS inactivity |
| `Config parsed successfully` | GPS config loaded and validated |

---

## NMEA Logging

When NMEA logging is enabled:
- Additional NMEA raw data files are created alongside regular GPS logs
- Log rotation applies to both GPS logs and NMEA logs
- KAM uploads both GPS and NMEA log archives

To enable NMEA logging:
```ini
[gps]
nmea_log_enable = true
```

---

## Service Dependencies

| Dependent Service | Interaction |
|------------------|-------------|
| `awsiot` | GPS publishes position data every 10 seconds |
| `speed` | Speed service reads GPS-based speed every 5 seconds |
| `kam` (Keep Alive Manager) | KAM zips and uploads GPS log files |
| `ndcentral` | NDCentral uses GPS data for position tracking |
| `ext_cam` | External camera uses GPS for tagging |
| `livestreaming` | Dual livestreaming uses GPS state (active/inactive) |

---

## Post-OTA Update Behavior

After an OTA update, GPS handle and initialization are verified to ensure GPS service recovers properly (TC_2558).

---

## API Reference

### ServiceController_obj
| Method | Parameters | Description |
|--------|-----------|-------------|
| `service_status` | `[["gps"]]` | Check GPS service status |
| `restart_service` | `[["gps"]]` | Restart GPS service |

### LogAnalyzer_obj
| Method | Parameters | Description |
|--------|-----------|-------------|
| `search_logs` | `["gps", ["pattern"]]` | Search GPS logs |
| `search_logs` | `["awsiot", ["pattern"]]` | Search AWSIOT logs for GPS publish |
| `search_logs` | `["speed", ["pattern"]]` | Search speed logs for GPS monitoring |

### UpdateConfig_obj
| Method | Parameters | Description |
|--------|-----------|-------------|
| `check_config_value` | `["gps", "key"]` | Read GPS config value |
| `download_config` | none | Download override config |
| `append_config_content` | `["content"]` | Append GPS config |
| `upload_config` | none | Upload config to device |

### Calculator_obj
| Method | Parameters | Description |
|--------|-----------|-------------|
| `get_device_info` | `["device_type"]` | Get device type |
| `run_command_on_device` | `["cmd"]` | Execute shell command on device |
| `intravel_check` | `[timestamps, interval]` | Verify data monitoring interval |

### DeviceController_obj
| Method | Parameters | Description |
|--------|-----------|-------------|
| `reboot_device` | none | Reboot device |
| `delay` | `[seconds]` | Wait specified seconds |
