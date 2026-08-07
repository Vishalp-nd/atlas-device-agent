---
name: gpslocaleswitch-service-validation
description: "Use when: running GPS Locale Switch service validation test cases on Netradyne devices. Covers locale transitions between regions (US, CA, MX, IN, AFG), nd_config.ini switching, missing config recovery, geo.ini handling, unsupported locale behavior, and reboot behavior across AWSIOT, SVC, watchdog, and ignition boot scenarios."
argument-hint: "device serial (e.g., /gpslocaleswitch-service-validation 2543fa04)"
---

# GPS Locale Switch Service Validation — Test Reference

This skill enables the **serial-testcase-executor** agent to run GPS Locale Switch service validation test cases on Netradyne devices (krait, krait2, bagheera2, bagheera3, octo).

## What is the GPS Locale Switch Service?

`gps_locale_switch` is a system service that:
- **Monitors GPS locale** — Detects when the vehicle crosses a geographic boundary (country border).
- **Switches nd_config.ini** — Swaps the device configuration file to the locale-appropriate version (e.g., `nd_config_US.ini`, `nd_config_CA.ini`, `nd_config_MX.ini`).
- **Loads locale-specific models** — Updates traffic sign and lane detection models for the new locale.
- **Updates geo.ini** — Writes the current locale to `/home/ubuntu/.nddevice/geo.ini`.
- **Triggers service restarts** — Notifies service_monitor of config switch; dependent services reload.
- **Handles missing configs** — Falls back to default US config when locale or recovery config is missing.

**Service name:** `gps_locale_switch`
**Config section:** `[locale_switch]` in `bagheera_override.ini`
**Geo file:** `/home/ubuntu/.nddevice/geo.ini`
**Config files:** `nd_config_US.ini`, `nd_config_CA.ini`, `nd_config_MX.ini`, `nd_config_IN.ini`

---

## Locale Switch Configuration

| Config Key | Section | Default | Description |
|-----------|---------|---------|-------------|
| `enable` | `[locale_switch]` | `0` | Enable locale switch (0=disabled, 1=enabled) |
| `duration` | `[locale_switch]` | `1` | Duration threshold for locale switch trigger |

To enable locale switching:
```ini
[locale_switch]
enable = 1
duration = 1

[power]
delay_reboot_time = 300
max_B2B_reboot_allowed = 50
```

---

## Supported Locales and Transitions

| From | To | Expected Behavior |
|------|----|------------------|
| US | CA | Switch to nd_config_CA.ini, load CA models, transfer CA geo.json |
| US | MX | Switch to nd_config_MX.ini, load MX models, transfer MX geo.json |
| US | IN | Switch to nd_config_IN.ini (if supported) |
| IN | AFG | Unsupported locale — should handle gracefully |
| US → IN | CA | Multi-hop: switch through IN then to CA |
| Any | US | Reset to default nd_config_US.ini |

---

## Key Log Patterns

| Pattern | Meaning |
|---------|---------|
| `Locale reset to default US completed successfully` | Reset to US locale |
| `Locale switch to CA is successful` | Switched to Canada locale |
| `Locale switch to MX is successful` | Switched to Mexico locale |
| `Locale switch to US is successful` | Switched back to US locale |
| `Complete critical info received by service monitor after nd_config.ini switch to default config nd_config_CA.ini` | Service monitor acknowledged CA switch |
| `TrafficSignEventTracker subclass for locale CA created successfully` | CA traffic sign models loaded |
| `CA models loaded successfully` | CA-specific ML models loaded |
| `US models loaded successfully` | US-specific ML models loaded |
| `countries.geo.json for CA switch transferred` | CA geofence data transferred |
| `countries.geo.json for CA switch moved to correct location` | CA geofence file placed |
| `After locale switch current nd_config.ini is pointing to {config_file}` | Active config verified |
| `Device reboot tracked successfully, reboot occurred due to locale switch` | Locale switch triggered reboot |

---

## Error Recovery Scenarios

### Missing nd_config Locale File (TC_2701)
When the locale-specific config file is missing, the service should fall back to default US config.

### Missing Default Config (TC_2702)
When the default nd_config.ini is missing, the service should recover gracefully.

### Missing Recovery Config — US to CA (TC_2703)
When recovery config is missing during US→CA switch, the service should handle the failure.

### Missing Recovery Config — US to MX (TC_2704)
Similar recovery test for US→MX transition.

### Missing geo.ini (TC_2709)
When geo.ini is missing, the locale switch should create it or use defaults.

### Unsupported Locale in geo.ini (TC_2710)
When geo.ini contains an unsupported locale value, the service should handle it gracefully.

---

## Reboot Scenarios

| Trigger | TC | Behavior |
|---------|-----|---------|
| AWSIOT reboot | TC_2736 | Locale switch state persists across AWSIOT-initiated reboot |
| SVC reboot | TC_2737 | Locale switch state persists across SVC-initiated reboot |
| Watchdog reboot | TC_2738 | Locale switch state persists across watchdog reboot |
| IGN ON boot | TC_2740 | Locale switch state verified on ignition-on boot up |

---

## Locale-Specific Models

| Locale | Traffic Sign Model | Traffic Sign Classifier |
|--------|-------------------|----------------------|
| US | `/autocam/tloc_US_v0` | `/autocam/tsc_v1.8.3` |
| CA | `/autocam/tloc_CA_v0.15.0.2` | `/autocam/tsc_ca_v2.1` |
| MX | Similar pattern | Similar pattern |

---

## API Reference

### ServiceController_obj
| Method | Parameters | Description |
|--------|-----------|-------------|
| `service_status` | `[["gps_locale_switch"]]` | Check locale switch service |

### UpdateConfig_obj
| Method | Parameters | Description |
|--------|-----------|-------------|
| `download_config` | none | Download override config |
| `append_config_content` | `["[locale_switch]\nenable = 1\nduration = 1\n..."]` | Enable locale switch |
| `upload_config` | none | Upload config |
| `reupload_config` | none | Restore original config |

### Calculator_obj
| Method | Parameters | Description |
|--------|-----------|-------------|
| `run_command_on_device` | `["cmd"]` | Execute shell command |
| `get_device_info` | `["device_type"]` | Get device type |

### DeviceController_obj
| Method | Parameters | Description |
|--------|-----------|-------------|
| `reboot_device` | none | Reboot device |
| `delay` | `[seconds]` | Wait specified seconds |
