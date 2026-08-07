---
name: connectionmanager-service-validation
description: "Use when: validating Connection Manager (conn_mgr) service behavior from device logs. Covers initialization, data session management, signal monitoring, band configuration, profile management, IPv6 handling, internet connectivity checks, modem reset, LU reject handling, SDK error reporting, and network info publishing."
argument-hint: "device ID (e.g., /connectionmanager-service-validation 103452403664)"
---

# Connection Manager (`conn_mgr`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the service —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads `.py` test case files from `tests/connectionmanager/`
> for actual log patterns, device-type paths, and acceptance criteria — this skill does NOT duplicate those.

---

## Service Overview

`conn_mgr` is a critical service that manages cellular connectivity for the device via Sierra Wireless (WP76xx) or Quectel (EC25) modems.
It handles modem initialization, APN/profile management, LTE data session establishment and monitoring, signal quality reporting, band configuration, IPv4/IPv6 session management, and internet connectivity checks.
The service interacts with `power_monitor`, `awsiot`, and `service_monitor` via the NDMU message queue (MSGQ) for publishing network status, and with the modem via the Sierra SLQS SDK or Quectel AT commands.

**Process name:** `conn_mgr`
**Log file:** `conn_mgr.log` (path: `/home/ubuntu/.nddevice/log/conn_mgr/` for bagheera2/bagheera3/octo, `/data/nd_files/log/conn_mgr/` for krait/krait2)
**Primary config sections:** `[conn_mgr_config]` (in `bagheera_override.ini` and `conn_mgr_config.txt`)

---

## Service Flows

### Flow 1: Initialization & Modem Enumeration

**What happens:** On startup, `conn_mgr` reads the band configuration file (`band_config.json`), parses override configs (`bagheera_override.ini`, `nd_core_common.ini`), enumerates the modem via Sierra SDK (`#devices: 1`, `deviceNode: /dev/qcqmi0`), reports SDK version, sets up signal handlers (sigsegv, sigterm, sigabrt), and creates the MSGQ message queue server.

**When active:** Always (every boot/restart)
**Frequency:** Once at service start
**Cross-service impact:** Message queue creation (`MSGQ: Message queue server created CONN_MGR`) enables network info publishing to other services.

**Key log patterns:**
- `CONN_MGR_MAIN: I: ... profile Name <PROFILE> & APN Name <APN> & deviceType <TYPE>`
- `CONN_MGR_UTIL_SIERRA: I: ... #devices: 1`
- `CONN_MGR_UTIL_SIERRA: I: ... SLQSSDK VERSION: SLQS04.00.27`
- `MSGQ: I: ... Message queue server created CONN_MGR`
- `CFG_PRSR: I: ... Override file ... present`
- `CFG_PRSR: I: ... OVerride file parsed successfully`
- `CONN_MGR_MAIN: I: ... Signal handler added for <signal>`

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_CONNECTIONMANAGER_2483` | `tests/connectionmanager/test_TC_connectionmanager_2483_validate_lumia_details.py` | Manufacture, Model Id, IMSI logged at boot |
| `TC_CONNECTIONMANAGER_2625` | `tests/connectionmanager/test_TC_connectionmanager_2625_startup_timing_measurement_of_impacted_services.py` | Service starts within boot window |
| `TC_CONNECTIONMANAGER_645` | `tests/connectionmanager/test_TC_connectionmanager_645_service_log_folders.py` | Log folders exist and contain files |
| `TC_CONNECTIONMANAGER_2562` | `tests/connectionmanager/test_TC_connectionmanager_2562_validate_profile_info_during_bootup.py` | PDP Type, Auth, Profile Name, APN, ESN, IMEI logged |

---

### Flow 2: Profile Management & APN Configuration

**What happens:** At startup, `conn_mgr` reads all modem profiles (`read_all_profiles`), identifies the configured APN from `conn_mgr_config.txt`, checks if the requested profile is already the default, and sets it via the Sierra SDK (`CheckProfileExistSetDefault`). It logs profile details (ID, PDP Type, Profile Name, APN Name).

**When active:** Always
**Frequency:** Once at boot
**Cross-service impact:** Determines which cellular network/APN the device connects to.

**Key log patterns:**
- `CONN_MGR_PROFILE: I: ... read_all_profiles`
- `CONN_MGR_PROFILE: I: ... Total number of profiles in device: <N>`
- `CONN_MGR_MAIN: I: ... read_all_profiles successful. Total <N> profiles present on the device`
- `CONN_MGR_PROFILE: I: ... CheckProfileExistSetDefault`
- `CONN_MGR_PROFILE: I: ... succesfully get default profile id <N>`
- `CONN_MGR_PROFILE: I: ... Requested apn already default profile`
- `CONN_MGR_INFO: I: ... Profile Name :<NAME>`
- `CONN_MGR_INFO: I: ... APN Name :<APN>`

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_CONNECTIONMANAGER_511` | `tests/connectionmanager/test_TC_connectionmanager_511_profile_update_tmo.py` | Profile updates correctly on APN change |
| `TC_CONNECTIONMANAGER_512` | `tests/connectionmanager/test_TC_connectionmanager_512_profile_update_jio_new_apn.py` | JIO new APN profile update |
| `TC_CONNECTIONMANAGER_513` | `tests/connectionmanager/test_TC_connectionmanager_513_profile_update_jio_old_apn.py` | JIO old APN profile update |
| `TC_CONNECTIONMANAGER_514` | `tests/connectionmanager/test_TC_connectionmanager_514_profile_update_telus_apn.py` | Telus APN profile update |
| `TC_CONNECTIONMANAGER_515` | `tests/connectionmanager/test_TC_connectionmanager_515_profile_update_att_apn.py` | ATT APN profile update |
| `TC_CONNECTIONMANAGER_521` | `tests/connectionmanager/test_TC_connectionmanager_521_profile_update_from_tmo_apn_to_other.py` | Profile switch from TMO to other |
| `TC_CONNECTIONMANAGER_522` | `tests/connectionmanager/test_TC_connectionmanager_522_profile_update_from_other_to_tmo_apn.py` | Profile switch from other to TMO |
| `TC_CONNECTIONMANAGER_2681` | `tests/connectionmanager/test_TC_connectionmanager_2681_invalid_profile_or_apn.py` | Invalid APN results in no connectivity |
| `TC_CONNECTIONMANAGER_2682` | `tests/connectionmanager/test_TC_connectionmanager_2682_corrupt_conn_mgr_txt.py` | Service survives corrupt config file |
| `TC_CONNECTIONMANAGER_1044` | `tests/connectionmanager/test_TC_connectionmanager_1044_reset_config.py` | Config reset to defaults |
| `TC_CONNECTIONMANAGER_652` | `tests/connectionmanager/test_TC_connectionmanager_652_check_unnecessary_apns.py` | No unnecessary APNs present |

---

### Flow 3: Data Session Management

**What happens:** After profile setup, `conn_mgr` starts a data session (`Starting Data session on profile Id <N>`). It monitors session state via callbacks (`SessionStateCallback:CONNECTED/DISCONNECTED`). On abrupt disconnection, it logs `Data session abruptly disconnected` with session duration and attempts reconnection. IPv4 and IPv6 sessions are managed separately (`IPv4 session - connected`, `IPv6 session - connected`).

**When active:** Always
**Frequency:** At boot and on session state changes (callbacks)
**Cross-service impact:** Data session connectivity enables all cloud services (awsiot, OTA, etc.)

**Key log patterns:**
- `CONN_MGR_MAIN: I: ... Starting Data session on profile Id <N>`
- `CONN_MGR_UTIL_SIERRA: I: ... LTE Data Session started successfully`
- `CONN_MGR_SESSION: I: ... LTE Data Session started successfully`
- `CONN_MGR_MAIN: I: ... Success : Data session connected`
- `CONN_MGR_CBK: I: ... SessionStateCallback:CONNECTED`
- `CONN_MGR_CBK: I: ... SessionStateCallback:DISCONNECTED`
- `CONN_MGR_CBK: I: ... SessionStateCallback: sessionEndReason = <N>`
- `CONN_MGR_MAIN: I: ... Data session abruptly disconnected, session <N> lasted <duration>`
- `CONN_MGR_UTIL_SIERRA: I: ... Session State: CONNECTED`
- `CONN_MGR_UTIL_SIERRA: I: ... 1. IPv4 session - connected: <handle>`
- `CONN_MGR_UTIL_SIERRA: I: ... 2. IPv6 session - connected: <handle>`

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_CONNECTIONMANAGER_2665` | `tests/connectionmanager/test_TC_connectionmanager_2665_validate_lte_interface_recovery_time.py` | LTE interface recovery after disruption |
| `TC_CONNECTIONMANAGER_2749` | `tests/connectionmanager/test_TC_connectionmanager_2749_validate_lumia_recover_from_lpm.py` | Recovery from low power mode (AT+CFUN=0/1) |

---

### Flow 4: Internet Connectivity Monitoring

**What happens:** A periodic thread checks for IP presence on the `eth1` interface. It reports `Internet is not present and ip is not present, no_ip_counter = <N>` when offline, and `Internet Connectivity Exists` when online. The check interval is ~300s. It also reads `max_internet_check_fail_limit` from config for recovery thresholds.

**When active:** Always
**Frequency:** Every ~300s (periodic)
**Cross-service impact:** Drives session restart logic when connectivity is lost.

**Key log patterns:**
- `CONN_MGR_UTIL_SIERRA: I: ... Check Ip For : eth1`
- `CONN_MGR_UTIL_SIERRA: I: ... Internet is not present and ip is not present, no_ip_counter = <N>`
- `CONN_MGR_UTIL_SIERRA: I: ... Internet Connectivity Exists`
- `CONN_MGR_UTIL_SIERRA: I: ... No ipv4 on interface, inet ; No ipv6 on interface, inet6 ;`
- `CFG_PRSR: E: ... isPresent: conn_mgr_config:max_internet_check_fail_limit not found`

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_CONNECTIONMANAGER_2565` | `tests/connectionmanager/test_TC_connectionmanager_2565_ip_address_validation.py` | IPV4 address present and valid format |
| `TC_CONNECTIONMANAGER_2665` | `tests/connectionmanager/test_TC_connectionmanager_2665_validate_lte_interface_recovery_time.py` | Recovery after interface down |

---

### Flow 5: Signal Quality Monitoring

**What happens:** A callback thread periodically reports LTE signal metrics: Signal Strength, RSRQ, RSRP, SINR, and Active Band Class via `CONN_MGR_INFO` and `CONN_MGR_CBK` (`RFInfoCallback`). These are logged at regular intervals (~10s) and used for network status publishing.

**When active:** Always (after modem registered)
**Frequency:** Every ~8-10s (callback driven)
**Cross-service impact:** Signal info is published via NDMU to healthstats/awsiot.

**Key log patterns:**
- `CONN_MGR_INFO: I: ... LTE - Signal Strength :<value>`
- `CONN_MGR_INFO: I: ... LTE - RSRQ :<value>`
- `CONN_MGR_INFO: I: ... LTE - RSRP :<value>`
- `CONN_MGR_INFO: I: ... LTE - SINR :<value>`
- `CONN_MGR_CBK: I: ... RFInfoCallback: activeBandClass = <value>`
- `CONN_MGR_INFO: I: ... Active Band Class :<value>`

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_CONNECTIONMANAGER_2554` | `tests/connectionmanager/test_TC_connectionmanager_2554_validate_signal_quality.py` | Signal quality (RSSI, RSRP, RSRQ, SNR) in logs |
| `TC_CONNECTIONMANAGER_2748` | `tests/connectionmanager/test_TC_connectionmanager_2748_validate_signal_info_payload_sendto_healthstats.py` | Signal payload sent to healthstats |

---

### Flow 6: Band Configuration

**What happens:** At startup, `conn_mgr` reads band configuration from `band_config.json` (if present) or uses original configuration logic based on APN name and modem model. It sets LTE band preferences and optionally WCDMA bands. The service logs current mode/band preferences from the device and whether changes are needed.

**When active:** Always at boot
**Frequency:** Once at startup
**Cross-service impact:** Determines which LTE bands the device uses for connectivity.

**Key log patterns:**
- `CONN_MGR_MAIN: I: ... Band configuration file not found: /home/ubuntu/band_config.json`
- `CONN_MGR_MAIN: I: ... No valid band configuration from file, using original configuration logic`
- `CONN_MGR_MAIN: I: ... APN name to be checked for band enabling is <APN>`
- `CONN_MGR_MAIN: I: ... LTE Band preference is already set as required for <MODEM>`
- `CONN_MGR_UTIL_SIERRA: I: ... Mode pref from device is <value>`
- `CONN_MGR_UTIL_SIERRA: I: ... LTE band pref from device is <hex>`
- `CONN_MGR_UTIL_SIERRA: I: ... LTE band pref ext from device bits_1_64 is <hex>`
- `CONN_MGR_MAIN: I: ... Band preference is already set as required`
- `CONN_MGR_MAIN: I: ... wcdma band enabled: FALSE`

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_CONNECTIONMANAGER_489` | `tests/connectionmanager/test_TC_connectionmanager_489_lte_bandconfig_tmo_lumia2.py` | TMO bands [2,4,12,25,26] on WP7611 |
| `TC_CONNECTIONMANAGER_490` | `tests/connectionmanager/test_TC_connectionmanager_490_lte_bandconfig_telstra_new_apn.py` | Telstra new APN bands |
| `TC_CONNECTIONMANAGER_491` | `tests/connectionmanager/test_TC_connectionmanager_491_lte_bandconfig_telstra_old_apn.py` | Telstra old APN bands |
| `TC_CONNECTIONMANAGER_492` | `tests/connectionmanager/test_TC_connectionmanager_492_lte_bandconfig_jio_new_apn.py` | JIO new APN bands |
| `TC_CONNECTIONMANAGER_493` | `tests/connectionmanager/test_TC_connectionmanager_493_lte_bandconfig_jio_old_apn.py` | JIO old APN bands |
| `TC_CONNECTIONMANAGER_494` | `tests/connectionmanager/test_TC_connectionmanager_494_lte_bandconfig_att.py` | ATT bands |
| `TC_CONNECTIONMANAGER_497` | `tests/connectionmanager/test_TC_connectionmanager_497_lte_bandconfig_tmo_apn_krait2.py` | TMO bands on krait2 |
| `TC_CONNECTIONMANAGER_498` | `tests/connectionmanager/test_TC_connectionmanager_498_lte_bandconfig_telus_canada.py` | Telus Canada bands |
| `TC_CONNECTIONMANAGER_499` | `tests/connectionmanager/test_TC_connectionmanager_499_lte_bandconfig_spark_nz.py` | Spark NZ bands |
| `TC_CONNECTIONMANAGER_500` | `tests/connectionmanager/test_TC_connectionmanager_500_lte_bandconfig_unknown_apn.py` | Unknown APN fallback bands |
| `TC_CONNECTIONMANAGER_501` | `tests/connectionmanager/test_TC_connectionmanager_501_lte_bandconfig_unknown_apn_wp7609.py` | Unknown APN on WP7609 |
| `TC_CONNECTIONMANAGER_503` | `tests/connectionmanager/test_TC_connectionmanager_503_lte_bandconfig_unknown_apn_wp7605.py` | Unknown APN on WP7605 |
| `TC_CONNECTIONMANAGER_507` | `tests/connectionmanager/test_TC_connectionmanager_507_lte_bandconfig_teal_lumia2.py` | Teal APN bands on Lumia2 |
| `TC_CONNECTIONMANAGER_510` | `tests/connectionmanager/test_TC_connectionmanager_510_lte_bandconfig_teal_wp7605.py` | Teal APN bands on WP7605 |
| `TC_CONNECTIONMANAGER_772` | `tests/connectionmanager/test_TC_connectionmanager_772_lte_bandconfig_teal_apn_krait2.py` | Teal bands on krait2 |
| `TC_CONNECTIONMANAGER_773` | `tests/connectionmanager/test_TC_connectionmanager_773_lte_bandconfig_unknown_apn_krait2.py` | Unknown APN on krait2 |
| `TC_CONNECTIONMANAGER_3333` | `tests/connectionmanager/test_TC_connectionmanager_3333_lte_bandconfig_wl_apn_uae.py` | WL APN UAE bands |
| `TC_CONNECTIONMANAGER_3340` | `tests/connectionmanager/test_TC_connectionmanager_3340_lte_bandconfig_roam11_apn_lumia2.py` | Roam11 APN Lumia2 bands |
| `TC_CONNECTIONMANAGER_3341` | `tests/connectionmanager/test_TC_connectionmanager_3341_lte_bandconfig_roam11_apn_lumia3.py` | Roam11 APN Lumia3 bands |
| `TC_CONNECTIONMANAGER_2589` | `tests/connectionmanager/test_TC_connectionmanager_2589_validate_band_and_configuration_info.py` | Band configuration info in logs |

---

### Flow 7: Network Mode Preference

**What happens:** `conn_mgr` reads the `wcdma_band_enabled` config key and sets the modem's network mode preference accordingly. When WCDMA is disabled, the device operates in LTE-only mode. The mode preference is verified via AT commands (`at!selrat?` for Sierra, `AT+QCFG="nwscanmode"` for EC25).

**When active:** Only when `[conn_mgr_config] wcdma_band_enabled` is configured
**Frequency:** Once at startup
**Cross-service impact:** Determines network access technology (LTE-only vs GSM+UMTS+LTE).

**Key log patterns:**
- `CFG_PRSR: I: ... No value present for key wcdma_band_enabled in override dictionary`
- `CONN_MGR_MAIN: I: ... wcdma band enabled: FALSE`
- `CONN_MGR_UTIL_SIERRA: I: ... Mode pref from device is <value>`

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_CONNECTIONMANAGER_480` | `tests/connectionmanager/test_TC_connectionmanager_480_default_network_mode.py` | Default mode is LTE when WCDMA disabled |
| `TC_CONNECTIONMANAGER_483` | `tests/connectionmanager/test_TC_connectionmanager_483_network_mode_wcdma_enabled_telstra_old_apn.py` | WCDMA enabled for Telstra old |
| `TC_CONNECTIONMANAGER_484` | `tests/connectionmanager/test_TC_connectionmanager_484_network_mode_wcdma_enabled_telstra_new_apn.py` | WCDMA enabled for Telstra new |
| `TC_CONNECTIONMANAGER_485` | `tests/connectionmanager/test_TC_connectionmanager_485_wcdma_config_enabled_tmo_apn.py` | WCDMA enabled for TMO |
| `TC_CONNECTIONMANAGER_486` | `tests/connectionmanager/test_TC_connectionmanager_486_wcdma_config_enabled_att_apn.py` | WCDMA enabled for ATT |
| `TC_CONNECTIONMANAGER_2715` | `tests/connectionmanager/test_TC_connectionmanager_2715_check_mode_preference_to_gsm_umts_lte.py` | Mode pref GSM+UMTS+LTE |
| `TC_CONNECTIONMANAGER_2716` | `tests/connectionmanager/test_TC_connectionmanager_2716_check_mode_preference_to_automatic.py` | Mode pref automatic |
| `TC_CONNECTIONMANAGER_2718` | `tests/connectionmanager/test_TC_connectionmanager_2718_check_mode_preference_to_umts_and_lte_only.py` | Mode pref UMTS+LTE only |
| `TC_CONNECTIONMANAGER_2720` | `tests/connectionmanager/test_TC_connectionmanager_2720_check_mode_preference_to_gsm_and_lte_only.py` | Mode pref GSM+LTE only |
| `TC_CONNECTIONMANAGER_2722` | `tests/connectionmanager/test_TC_connectionmanager_2722_check_mode_preference_lte_only.py` | Mode pref LTE only |

---

### Flow 8: IPv6 Session Management

**What happens:** `conn_mgr` reads `ipv6_session_disabled` from the override config. When IPv6 is enabled (default), it starts both IPv4 and IPv6 data sessions and resets the eth1 interface for IPv6. It periodically logs IPv4 and IPv6 addresses. When IPv6 is disabled, only IPv4 session is established.

**When active:** Always (behavior depends on `ipv6_session_disabled` config)
**Frequency:** At session start and periodic monitoring
**Cross-service impact:** Determines dual-stack vs IPv4-only connectivity.

**Key log patterns:**
- `CFG_PRSR: I: ... No value present for key ipv6_session_disabled in override dictionary`
- `CONN_MGR_MAIN: I: ... ipv6 session disabled: FALSE`
- `CONN_MGR_MAIN: I: ... eth1 interface reset for IPV6`
- `CONN_MGR_UTIL_SIERRA: I: ... Global IPV6 Address: <addr>`
- `CONN_MGR_UTIL_SIERRA: I: ... IPV4 Address: <addr>`
- `CONN_MGR_UTIL_SIERRA: I: ... IPV6 Address: <addr>`
- `Start IPv4v6 LTE data session( Instance: 0 ) status:`

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_CONNECTIONMANAGER_2592` | `tests/connectionmanager/test_TC_connectionmanager_2592_disable_ipv6_config.py` | IPv6 disabled — no IPv6 address on eth1 |
| `TC_CONNECTIONMANAGER_2565` | `tests/connectionmanager/test_TC_connectionmanager_2565_ip_address_validation.py` | Valid IPv4 address in logs |

---

### Flow 9: Network Info Publishing (NDMU)

**What happens:** Periodically (~60s), `conn_mgr` publishes network status information (SIM number, IMEI, RX/TX bytes, signal info) via the NDMU message queue. This data is consumed by other services for cloud reporting.

**When active:** Always (after session established)
**Frequency:** Every ~60s
**Cross-service impact:** Provides network telemetry to awsiot/healthstats services.

**Key log patterns:**
- `NDMU: I: ... calling publish`
- `NDMU: I: ... pub message sent`
- `CONN_MGR_MAIN: I: ... Publish Network Successful For <timestamp>`
- `CONN_MGR_MAIN: I: ... sim no in send = <SIM>`
- `CONN_MGR_MAIN: I: ... imei no in send = <IMEI>`
- `CONN_MGR_UTIL_SIERRA: I: ... RX Bytes: <value>`
- `CONN_MGR_UTIL_SIERRA: I: ... TX Bytes: <value>`

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_CONNECTIONMANAGER_2748` | `tests/connectionmanager/test_TC_connectionmanager_2748_validate_signal_info_payload.py` | Signal payload published to healthstats |

---

### Flow 10: Modem Reset Handling

**What happens:** `conn_mgr` reads the `modem_reset` config key. When enabled, it can trigger modem resets under certain failure conditions. It also reads and logs modem reset info (`Reset Info: type: <N>, source: <N>`). Modem log capturing can be enabled/disabled via `modem_log_enabled`.

**When active:** Only when `[conn_mgr_config] modem_reset` is enabled
**Frequency:** On error conditions
**Cross-service impact:** Modem reset causes temporary connectivity loss; all dependent services affected.

**Key log patterns:**
- `CFG_PRSR: I: ... No value present for key modem_reset in override dictionary`
- `CONN_MGR_MAIN: I: ... modem reset is: FALSE`
- `CONN_MGR_MAIN: I: ... Modem_Reset Config Read = 0`
- `CONN_MGR_UTIL_SIERRA: I: ... Reset Info: type: <N>, source: <N>`
- `CONN_MGR_MAIN: I: ... modem log enabled: FALSE`
- `CONN_MGR_UTIL_SIERRA: I: ... Modem log capturing is disabled, remove the old log`

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_CONNECTIONMANAGER_2563` | `tests/connectionmanager/test_TC_connectionmanager_2563_validate_lumia_reset_functionality.py` | Lumia reset functionality |

---

### Flow 11: LU Reject Handling

**What happens:** When a Location Update (LU) reject is received from the network, `conn_mgr` processes the reject cause code. Depending on the cause (e.g., cause 15), the device may remain registered to LTE or may be deregistered. The service monitors PS state (Attached/Detached) after LU reject events.

**When active:** On network LU reject events
**Frequency:** Event-driven
**Cross-service impact:** May cause temporary loss of LTE registration; critical event reported.

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_CONNECTIONMANAGER_463` | `tests/connectionmanager/test_TC_connectionmanager_463_validate_lureject_cause_device_remain_register_to_lte.py` | Device stays registered after LU reject (cause 15) |
| `TC_CONNECTIONMANAGER_469` | `tests/connectionmanager/test_TC_connectionmanager_469_lureject_cause_device_deregistered_lte.py` | Device deregisters after fatal LU reject |
| `TC_CONNECTIONMANAGER_487` | `tests/connectionmanager/test_TC_connectionmanager_487_validate_multiple_lureject_device_registered_lte.py` | Multiple rejects — stays registered |
| `TC_CONNECTIONMANAGER_488` | `tests/connectionmanager/test_TC_connectionmanager_488_validate_multiple_lureject_cause_device_oos.py` | Multiple rejects — goes OOS |
| `TC_CONNECTIONMANAGER_555` | `tests/connectionmanager/test_TC_connectionmanager_555_validate_lureject_cause_critical_info.py` | LU reject logged as critical info |

---

### Flow 12: SDK Error Handling & Critical Events

**What happens:** When Sierra SDK errors occur (e.g., error code 6 — module not enumerated, or 57346), `conn_mgr` logs them as critical events in `sm_critical_events.json`. Error code 6 typically triggers a service restart. Other errors may be logged but not acted upon.

**When active:** On SDK errors
**Frequency:** Event-driven
**Cross-service impact:** Critical events reported to service_monitor; may trigger service restart.

**Key log patterns:**
- `CONN_MGR_SESSION: E: ... Modem not registered to network, reg state 255`
- `CONN_MGR_UTIL_SIERRA: I: ... QMI response error or device disconnect`
- `CONN_MGR_INFO: I: ... Device State :DEVICE DISCONNECTED`

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_CONNECTIONMANAGER_552` | `tests/connectionmanager/test_TC_connectionmanager_552_critical_info_sdk_error_6_module_enumerated.py` | SDK error 6 logged as critical event |
| `TC_CONNECTIONMANAGER_558` | `tests/connectionmanager/test_TC_connectionmanager_558_service_restart_sdk_error_6.py` | Service restarts on SDK error 6 |
| `TC_CONNECTIONMANAGER_625` | `tests/connectionmanager/test_TC_connectionmanager_625_behaviour_critical_sdk_error_57346.py` | SDK error 57346 handling |
| `TC_CONNECTIONMANAGER_630` | `tests/connectionmanager/test_TC_connectionmanager_630_behaviour_sdk_error_other_than_6.py` | Non-critical SDK error handling |
| `TC_CONNECTIONMANAGER_637` | `tests/connectionmanager/test_TC_connectionmanager_637_behaviour_sdk_error_6_at_service_starting.py` | SDK error 6 during boot |

---

### Flow 13: PSM (Power Saving Mode) & Emergency Mode

**What happens:** For WP76xx modems, `conn_mgr` ensures PSM is disabled (AT+CPSMS? returns 0) and verifies the device is not in emergency-only mode. These are validated via AT commands post-startup.

**When active:** WP76xx modems only
**Frequency:** Once after startup
**Cross-service impact:** PSM enabled would cause periodic connectivity loss; emergency-only would block data.

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_CONNECTIONMANAGER_650` | `tests/connectionmanager/test_TC_connectionmanager_650_psm_status.py` | PSM is disabled (value=0) |
| `TC_CONNECTIONMANAGER_655` | `tests/connectionmanager/test_TC_connectionmanager_655_emergency_mode_check.py` | No emergency-only mode active |

---

### Flow 14: Network Registration & AT Command Interface

**What happens:** `conn_mgr` monitors modem network registration state. It reports registered MCC/MNC values and handles registration failures. For Sierra modems, AT commands are sent via `lte_gps_sample_app`. The service logs device info (Manufacture, Model Id, Firmware, Hardware Revision, IMSI, IMEI, MEID, ESN, SIM number).

**When active:** Always
**Frequency:** At boot and on registration state changes
**Cross-service impact:** Registration state directly impacts data session availability.

**Key log patterns:**
- `CONN_MGR_UTIL_SIERRA: I: ... Device is not registered to network`
- `CONN_MGR_UTIL_SIERRA: I: ... registered mcc string = <MCC>`
- `CONN_MGR_UTIL_SIERRA: I: ... registered mnc string = <MNC>`
- `CONN_MGR_INFO: I: ... Manufacture :<name>`
- `CONN_MGR_INFO: I: ... Model Id :<model>`
- `CONN_MGR_INFO: I: ... Firmware Revisions :<fw>`
- `CONN_MGR_INFO: I: ... IMEI Number :<imei>`
- `CONN_MGR_UTIL_SIERRA: I: ... getIMSI: IMSI: <imsi>`
- `CONN_MGR_UTIL_SIERRA: I: ... Fetched sim number : <iccid>`

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks |
|---|---|---|
| `TC_CONNECTIONMANAGER_2483` | `tests/connectionmanager/test_TC_connectionmanager_2483_validate_lumia_details.py` | Manufacture, Model Id, IMSI validated |
| `TC_CONNECTIONMANAGER_2711` | `tests/connectionmanager/test_TC_connectionmanager_2711_validate_at_commands_across_lumia_type.py` | AT commands work across lumia types |
| `TC_CONNECTIONMANAGER_2748` | `tests/connectionmanager/test_TC_connectionmanager_2748_validate_signal_info_payload.py` | Signal info via AT+CSQ |

---

## Config-Driven Flow Activation

The agent MUST read the device config from `device_data/device_<ID>_config.ini`
before selecting test cases. Use the following mapping:

| Config Section      | Config Key                    | Value         | Activates Flow(s)                        | Test Cases Affected                                           |
|---|---| ---------------------------------------- | ------------------------------------------------------------- |
| `[conn_mgr_config]` | `wcdma_band_enabled`         | `true`        | Network Mode Preference (WCDMA enabled)  | `TC_483`, `TC_484`, `TC_485`, `TC_486`                        |
| `[conn_mgr_config]` | `wcdma_band_enabled`         | `false`       | Network Mode Preference (LTE only)       | `TC_480`, `TC_2715`–`TC_2722`                                 |
| `[conn_mgr_config]` | `ipv6_session_disabled`      | `true`        | IPv6 Session Management (disabled)       | `TC_2592`                                                     |
| `[conn_mgr_config]` | `ipv6_session_disabled`      | `false`/absent| IPv6 Session Management (enabled)        | `TC_2565`                                                     |
| `[conn_mgr_config]` | `modem_reset`                | `true`        | Modem Reset Handling (enabled)           | `TC_2563`                                                     |
| `[conn_mgr_config]` | `modem_log_enabled`          | `true`        | Modem log capturing                      | —                                                             |
| `[conn_mgr_config]` | `max_internet_check_fail_limit` | `<N>`      | Internet check threshold                 | `TC_2665`                                                     |
| —                   | —                             | —             | Initialization (always active)           | `TC_2483`, `TC_2562`, `TC_2625`, `TC_645`                     |
| —                   | —                             | —             | Data Session Management (always active)  | `TC_2665`, `TC_2749`                                          |
| —                   | —                             | —             | Signal Monitoring (always active)        | `TC_2554`, `TC_2748`                                          |
| —                   | —                             | —             | Profile Management (always active)       | `TC_511`–`TC_522`, `TC_2681`, `TC_2682`, `TC_1044`, `TC_652`  |
| —                   | —                             | —             | Band Configuration (always active)       | `TC_489`–`TC_510`, `TC_772`, `TC_773`, `TC_3333`, `TC_3340`, `TC_3341` |
| —                   | —                             | —             | Network Info Publishing (always active)  | `TC_2748`                                                     |

**Rules:**
- Flows marked "always active" → run their test cases unconditionally
- Flows gated by a config key → run only if the device config has that key set to the activating value
- If the config key is missing from the device config → use the default value listed above
- Config values in `device_list_config.csv` take precedence if present (they reflect live production config)
- Band configuration test cases are APN-specific — select based on the device's configured APN in `conn_mgr_config.txt`
- Mode preference test cases depend on modem model (WP7611/WP7609/WP7605 for Sierra, EC25 for Quectel)

---

## Cross-Service Dependencies

| Related Service    | Why                                                        | When to check its logs                    |
|---|---|
| `power_monitor`    | Manages device power state; reboots affect conn_mgr        | When validating session recovery after reboot |
| `service_monitor`  | Monitors conn_mgr health; restarts on critical errors      | When validating SDK error handling         |
| `awsiot`           | Consumes network info published by conn_mgr via NDMU       | When validating network info publishing    |
| `nd_suspendresume` | Suspend/resume cycle affects connectivity                  | When validating session recovery           |

---

## Flow Dependency Graph

```
boot → [Flow: Initialization] → modem enumerated, SDK ready
                              → [Flow: Profile Management] → APN set, default profile configured
                              → [Flow: Band Configuration] → LTE bands set per APN
                              → [Flow: Network Mode] → LTE-only or multi-mode set
                              → [Flow: Data Session] → IPv4+IPv6 connected
                                                     → [Flow: Internet Monitoring] → periodic checks (300s)
                                                     → [Flow: Signal Monitoring] → periodic callbacks (~10s)
                                                     → [Flow: Network Info Publishing] → NDMU publish (60s)
event (session disconnect) → [Flow: Data Session] → auto-reconnect
event (LU reject) → [Flow: LU Reject Handling] → stay registered or deregister
event (SDK error) → [Flow: SDK Error Handling] → critical event + possible restart
config (modem_reset=true) → [Flow: Modem Reset] → reset on persistent failures
config (ipv6_session_disabled=true) → [Flow: IPv6 Management] → IPv4-only session
```

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Determine active flows** using the Config-Driven Flow Activation table above
3. **Identify APN and modem model** from config or device logs to select band config test cases
4. **For each active flow**, read the mapped `.py` test case files from `tests/connectionmanager/` and use the log patterns from this skill
5. **Search device logs** in `device_logs/<device_id>/` for `conn_mgr.log` using patterns from this skill
7. **For cross-service checks**, also search logs of `power_monitor`, `service_monitor` as listed above
8. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED
