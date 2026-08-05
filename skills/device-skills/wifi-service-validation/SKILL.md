---
name: wifi-service-validation
description: "Use when: validating WiFi Manager (wifi_mgr) service behavior from device logs. Covers service initialization, SSID scanning & connection, wifi fallback, hotspot management (AP mode), ping/internet monitoring, route metric management, health stats wifi payload, speed service registration, ignition handling, DTA (Driver Training App) mode, external camera (DHUB) wifi mode, wlan0 interface monitoring, and service stability across reboots/LPW."
argument-hint: "device serial (e.g., /wifi-service-validation 2543fa04)"
---

# WiFi Manager Service (`wifi_mgr`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the WiFi Manager service —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads `.py` test case files from `tests/wifi/`
> for actual log patterns, device-type paths, and acceptance criteria — this skill does NOT duplicate those.

---

## Service Overview

`wifi_mgr` is an always-running service that manages WiFi connectivity on Netradyne devices. It handles SSID scanning and connection (STA mode), hotspot creation (AP mode) for installer app and DHUB, internet ping monitoring with response-time thresholds, route metric management to prioritize wifi over LTE, health stats wifi payload publishing, speed service registration for idle/DTA behavior, and external camera (DHUB/VBUS) connectivity. The service interacts with power_monitor (ignition status), speed service (registration), ndcentral (health stats), and ext_cam (DHUB mode) via message queues.

**Process name:** `wifi_mgr` (binary: `wifi_mgr`)
**Log file:** `wifi_mgr.log` (path: `/home/ubuntu/.nddevice/log/wifi_mgr/`)
**Primary config sections:** `[wifi]`, `[ext_cam]`, `[ext_cam_settings]`, `[ext_cam_config]`, `[driverlogin]`
**Config files:** `bagheera_config.ini`, `bagheera_override.ini`, `automation_config.ini`
**Message queue name:** `WIFI_MGR` (server)
**Interface:** `wlan0` (primary), `secondary` (secondary/ext_cam)

---

## Service Flows

### Flow 1: Service Initialization & Configuration Parsing

**What happens:** On startup, the service logs the starting banner, detects WiFi module type (Azurewave/QCA), reads `bagheera_override.ini` for overrides, parses `[wifi]` section from `bagheera_config.ini` for hotspot_interface, hotspot_channel, max_disconnections, operation_mode. Also reads `[driverlogin]` for login_speed threshold. Creates the message queue (`WIFI_MGR`), checks for power_monitor and speed services, and registers with speed service.

**When active:** Always (on every service start/restart)
**Frequency:** Once at boot / on service restart
**Cross-service impact:** speed service depends on wifi_mgr registration; service_mon monitors this service

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `**********Starting WIFI MGR Service**********` | Service startup marker |
| `Override file /home/ubuntu/config/bagheera_override.ini present` | Override config detected |
| `OVerride file parsed successfully` | Override config applied |
| `DeviceType: <type>` | Device type detected |
| `Azurewave Wi-Fi Module Detected` | Azurewave WiFi module found |
| `QCA Wi-Fi Module Detected` | QCA WiFi module found |
| `Message queue server created WIFI_MGR` | MSGQ created |
| `Message queue created` | MSGQ ready |
| `Checking For q_power_monitor Service` | Checking power_monitor availability |
| `Checking For SPEED Service` | Checking speed service availability |
| `Speed Service Registration Successfull` | Registered with speed service |
| `WIFI Hotspot Will Be Configured on wlan0` | Hotspot interface configured |
| `Driver Login Speed Is : <N>` | DTA speed threshold set |
| `wifi_connect_task_timeout_secs: <N>` | Connection timeout configured |
| `Ping response threshold ms: <N>` | Ping threshold configured |
| `ssid_availability_check_interval_secs: <N>` | SSID scan interval configured |
| `wifi_conn_test_interval_secs: <N>` | Connection test interval configured |
| `wifi_reconnect_interval_msecs: <N>` | Reconnect interval configured |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_wifi_1723` | `tests/wifi/test_tc_wifi_1723_service_status.py` | Service is active/running | — |
| `TC_wifi_1724` | `tests/wifi/test_tc_wifi_1724_override_parsed_successfully.py` | Override config parsed | `DT-3765`, `DT-3592` |
| `TC_wifi_1736` | `tests/wifi/test_tc_wifi_1736_verify_message_queue.py` | MSGQ creation | — |
| `TC_wifi_3263` | `tests/wifi/test_tc_wifi_3263_service_status_network_available.py` | Service status with network | `BG4-971`, `BGR3-1456` |
| `TC_wifi_3265` | `tests/wifi/test_tc_wifi_3265_service_status_no_network.py` | Service status without network | — |
| `TC_wifi_3268` | `tests/wifi/test_tc_wifi_3268_files_integrity_verification.py` | Binary and files integrity | — |

---

### Flow 2: SSID Scanning & WiFi Connection (STA Mode)

**What happens:** The service periodically scans for configured SSIDs using `iw dev wlan0 scan` (krait/bagheera3/4) or `iwlist wlan0 scan` (bagheera2). Scan results are written to `/dev/shm/wlan_scan_result.txt`. When a configured SSID is found, it initiates connection via `nmcli dev wifi connect` (bagheera) or `wpa_supplicant + dhcpcd` (krait). The connection is tested for internet by pinging 8.8.8.8 with a response time threshold. If connection is slow (ping > threshold), it disconnects and retries after the reconnect interval.

**When active:** When `wifi_fallback = enable` in `[wifi]` config OR DTA (automation) mode
**Frequency:** Every `ssid_availability_check_interval_secs` (default 30s)
**Cross-service impact:** Route metric altered to prefer wifi; affects LTE connectivity path

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `found wlan0 interface` | wlan0 interface is up |
| `ssid_found: 0` | No configured SSID found in scan |
| `ssid_found: 1` | Configured SSID found in scan |
| `home ssid: <name> found` | Specific SSID matched |
| `ssid <name> is available` | SSID available for connection |
| `initiating wifi connection for ssid <name>` | Connection attempt starting |
| `connect cmd: <cmd>` | Connection command being executed |
| `Wifi Connected to <ssid> on <iface>` | Connection successful (bagheera) |
| `wifi connect resp empty` | Connection response empty (failure) |
| `Wifi connect timed task failed with result <N>` | Connection timed out |
| `Wifi Is Already Disconnected` | Already disconnected |
| `Wifi disconnected` | Disconnect successful (bagheera2) |
| `Installer App Active - Ignoring the Wifi Connect Request` | Blocked by installer |
| `Active Scan cmd = iw dev wlan0 scan` | Active scan initiated |
| `WIFI_SCAN_PASSIVE Scan cmd = iw dev wlan0 scan passive` | Passive scan initiated |
| `WIFI_SCAN_ACTIVE Scan cmd = iw dev wlan0 scan` | Active scan initiated |
| `Station is Already Up, No Need to disable hotspot` | Already in STA mode |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_wifi_1722` | `tests/wifi/test_tc_wifi_1722_check_wifi_fallback_feature.py` | WiFi fallback feature enabled | `BG4-789` |
| `TC_wifi_1725` | `tests/wifi/test_tc_wifi_1725_wifiname_found_inconfig.py` | WiFi name found in config | `BG4-971`, `BGR3-1456`, `DT-3725`, `BG4-513` |
| `TC_wifi_1728` | `tests/wifi/test_tc_wifi_1728_verify_behaviour_after_prolonged.py` | SSID scan after prolonged ignition off | — |
| `TC_wifi_1733` | `tests/wifi/test_tc_wifi_1733_data_connectivity_lte_wifi.py` | Data connectivity on wifi+LTE | `BG4-789`, `OCTO-2108` |
| `TC_wifi_1735` | `tests/wifi/test_tc_wifi_1735_verify_internet_disconnection.py` | Internet disconnection handling | `BG4-986` |
| `TC_wifi_2232` | `tests/wifi/test_tc_wifi_2232_data_connectivity_lte_wifi_ignition_off.py` | Connectivity after ignition off | — |
| `TC_wifi_2235` | *(no .py file — test case not yet implemented)* | Connectivity during LPM | `OCTO-2108` |
| `TC_wifi_2239` | `tests/wifi/test_tc_wifi_2239_internet_reconnection_post_wifi_disconnect_ignition_off.py` | Reconnection post disconnect + ign off | `BG4-986`, `DT-3725`, `BG4-552` |

---

### Flow 3: Ping / Internet Connectivity Monitoring

**What happens:** Once connected to WiFi, the service periodically pings 8.8.8.8 (or configured gateway) to verify internet connectivity. Ping response time is compared against `ping_response_threshold_msecs` (default 5000ms). If ping fails or exceeds threshold for consecutive checks, the service disconnects WiFi and enters reconnect wait (`wifi_reconnect_interval_msecs`). If internet is available, it continues monitoring at `wifi_connection_test_interval_secs` (default 60s).

**When active:** After WiFi connected in STA mode
**Frequency:** Every `wifi_connection_test_interval_secs` (default 60s)
**Cross-service impact:** Determines if data flows over WiFi or falls back to LTE

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `ping cmd: <cmd>` | Ping command being executed |
| `ping: response time: <N>` | Ping response time in ms |
| `No internet connectivity` | Ping failed — no internet |
| `ip became unavailable. Disconnecting` | IP lost, disconnecting WiFi |
| `Attempt to connect to home ssid failed` | Reconnect failed |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_wifi_1732` | `tests/wifi/test_tc_wifi_1732_verify_ping_request.py` | Ping request verification | — |
| `TC_wifi_1735` | `tests/wifi/test_tc_wifi_1735_verify_internet_disconnection.py` | Disconnection on no internet | `BG4-986` |
| `TC_wifi_2236` | `tests/wifi/test_tc_wifi_2236_reconnection_post_disconnection_lpm.py` | Reconnection after disconnect in LPM | — |

---

### Flow 4: Hotspot / AP Mode Management

**What happens:** The service can switch to AP mode for installer app connectivity or DHUB pairing. On receiving `CREATE_INSTALLER_HOTSPOT` message, it creates a hotspot using `nmcli` (bagheera) or `hostapd + dnsmasq` (krait). Hotspot SSID is derived from device_id MD5 hash. The service monitors hotspot health by checking dnsmasq status and restarting if needed. Max disconnections trigger dnsmasq restart. On `STOP_INSTALLER_HOTSPOT`, it reverts to STA mode.

**When active:** On installer app request or DHUB pairing
**Frequency:** On-demand via MSGQ
**Cross-service impact:** Installer app uses hotspot for device configuration; DHUB connects via hotspot

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `got the instruction to enable hotspot` | Hotspot creation requested |
| `Hotspot Is Already Created` | Hotspot already up |
| `Hotspot Brought Up With Command <cmd>` | Hotspot successfully created |
| `Hotspot Brought Down With Command <cmd>` | Hotspot brought down |
| `hotspot creation failed - retrying` | Hotspot creation failure (retry) |
| `Hotspot Creation Failed, Exiting ...` | Hotspot creation failed permanently |
| `Installer App Active File Created` | Installer app mode activated |
| `received message to stop installer hotspot` | Stop hotspot request |
| `Disabling Hotspot` | Hotspot teardown started |
| `Not Monitoring As Hotspot Is Not Created` | Hotspot monitoring skipped |
| `Hotspot is up with IP: <ip>` | Hotspot verified with IP |
| `Hotspot is not up after waiting <N> secs` | Hotspot failed verification |
| `Dnsmasq Is Not Up On Wlan0` | Dnsmasq down, needs restart |
| `Successufully Restarted Dnsmasq` | Dnsmasq restarted |
| `Max Disconnections(<N> mins) Reached` | Max disconnect threshold hit |
| `Station is Already Up, No Need to disable hotspot` | STA mode active |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_wifi_1727` | `tests/wifi/test_tc_wifi_1727_check_extcam_status.py` | External camera/DHUB hotspot | `DT-4291`, `DT-4288`, `DT-4264` |

---

### Flow 5: Route Metric Management

**What happens:** After WiFi connects, the service alters the routing table to prioritize WiFi over LTE. On krait: deletes default route on wlan interface. On bagheera: changes route metric — sets wlan0 metric to 99 (lower than eth0/eth1 at 100) so traffic prefers WiFi. If WiFi disconnects, routes revert to LTE. The route alteration uses `ip route add/del` with metric manipulation.

**When active:** After successful WiFi connection
**Frequency:** On WiFi connect/disconnect
**Cross-service impact:** Affects all outbound traffic routing; LTE remains backup

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `delete route cmd: <cmd>` | Route deletion (krait) |
| `failed to run cmd to delete default route entry` | Route delete failed |
| `<iface> route del cmd: <cmd>` | Route delete command |
| `<iface> route add resp: <cmd>` | Route add command |
| `failed to del default <iface> route` | Route delete failure |
| `failed to add default route with altered metric` | Metric alteration failed |
| `eth0 route metric set as required` | Eth0 metric configured |
| `<iface> route metric set as required` | WiFi route metric set |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_wifi_1733` | `tests/wifi/test_tc_wifi_1733_data_connectivity_lte_wifi.py` | Data flows via WiFi when both available | `BG4-789`, `OCTO-2108` |
| `TC_wifi_2232` | `tests/wifi/test_tc_wifi_2232_data_connectivity_lte_wifi_ignition_off.py` | Route preference after ignition cycle | — |
| `TC_wifi_2235` | *(no .py file — test case not yet implemented)* | Route preference during LPM | `OCTO-2108` |

---

### Flow 6: Health Stats WiFi Payload

**What happens:** On receiving `REQ_HEALTH_INFO` message from ndcentral, the service collects current WiFi status (MAC address, SSID, IPv4, IPv6, signal strength, routes, connected clients) and formats it as JSON. Publishes to health stats via `send_msg_healthstats`. Fields: `ts` (13-digit epoch), `mac_id`, `ssid`, `status` (bool), `ipv4s`, `ipv6s`, `signal_strength`, `routes`, `clients`. Also sends secondary WiFi info if secondary interface exists.

**When active:** On health info request from ndcentral
**Frequency:** On-demand (tied to health stats publish cycle, typically every 2 minutes)
**Cross-service impact:** ndcentral aggregates wifi info into full health payload; uploader sends to cloud

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `RECIEVED REQ_WIFI_NETWORK_INFO` | Health info request received |
| `health msg = <json>` | WiFi health payload constructed |
| `sec health msg = <json>` | Secondary WiFi health payload |

**WiFi info collection commands (internal):**
| Command | What it collects |
|---------|---------|
| `ip route \| grep wlan0` | Route info |
| `ip -4 addr show dev wlan0 \| grep inet \| grep global \| awk '{print $2;}'` | IPv4 address |
| `ip -6 addr show dev wlan0 \| grep inet6 \| grep global \| awk '{print $2;}'` | IPv6 address |
| `ip link show dev wlan0 \| grep link \| awk '{print($2)}'` | MAC address |
| `iwconfig wlan0 \| grep ESSID \| awk '{print($3)}' \| cut -d '"' -f 2` | SSID (krait/bagheera3/4) |
| `iw dev wlan0 link \| grep signal \| cut -d ' ' -f 2` | Signal strength (krait/bagheera3/4) |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_wifi_1779` | `tests/wifi/test_tc_wifi_1779_verify_wifi_fields_health_payload.py` | WiFi fields in health payload | `BG4-512` |
| `TC_wifi_2231` | `tests/wifi/test_tc_wifi_2231_wifi_fields_health_payload_ignition_off.py` | Health payload after ignition off | — |
| `TC_wifi_2234` | *(no .py file — test case not yet implemented)* | Health payload during LPM | — |
| `TC_wifi_1755` | `tests/wifi/test_tc_wifi_1755_observation_payload.py` | Observation payload (get_network_info) | — |
| `TC_wifi_2237` | `tests/wifi/test_tc_wifi_2237_observation_payload_ignition_low.py` | Observation payload ign off | — |
| `TC_wifi_2238` | *(no .py file — test case not yet implemented)* | Observation payload LPM | — |
| `TC_wifi_3401` | `tests/wifi/test_tc_wifi_3401_check_video_upload_metadata_incab_feedback.py` | WiFi connection info in video upload metadata for InCab feedback | — |

---

### Flow 7: Speed Service Integration & Idle/DTA

**What happens:** The service registers with the speed service to receive speed updates. On receiving `RES_SPEED_UPDATE`, it stores current_speed. When speed exceeds `login_speed` threshold (from `[driverlogin]` config), SSID scanning is suppressed if both ext_cam and iosix are disabled (optimization to avoid unnecessary scans at high speed). DTA (Driver Training App) mode uses automation_config.ini for WiFi credentials instead of bagheera_config.ini.

**When active:** Always — speed registration at startup
**Frequency:** On speed update messages (continuous)
**Cross-service impact:** speed service sends updates; DTA mode alters wifi credential source

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `Speed Service Registration Successfull` | Registered with speed service |
| `Speed Service Un-Registration Successfull` | Unregistered from speed |
| `Not Scanning as both ext_cam_enabled and iosix_enabled are disabled` | Scan suppressed (speed) |
| `Scanning as IP does not exist` | Scan triggered (no IP) |
| `Not Scanning as IP Exists` | Scan suppressed (IP exists) |
| `TEST wifi name found in cfg file is <name>` | DTA/automation SSID loaded |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_wifi_1733` | `tests/wifi/test_tc_wifi_1733_data_connectivity_lte_wifi.py` | Data connectivity | `BG4-789`, `OCTO-2108` |

---

### Flow 8: Ignition Status Handling

**What happens:** The service receives `POWERMON_IGNITION` messages from power_monitor via MSGQ. It updates internal ignition_status flag. When ignition is OFF and wlan0 interface error occurs, the service suppresses error reporting to cloud (SM_E_WMGR_WLAN_ERR) since wlan0 may be legitimately down. Ignition transitions affect scanning behavior.

**When active:** Always — on ignition status change
**Frequency:** On every ignition change event
**Cross-service impact:** power_monitor sends ignition status; affects error reporting to service_mon

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `Ign status = <N>, crank_change_time = <N>, lpw_status = <N>` | Ignition message received |
| `Error in reading ignition status` | Invalid ignition status value |
| `wlan0 interface not found` | wlan0 missing (error) |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_wifi_1728` | `tests/wifi/test_tc_wifi_1728_verify_behaviour_after_prolonged.py` | Behavior after prolonged ign off | — |
| `TC_wifi_2232` | `tests/wifi/test_tc_wifi_2232_data_connectivity_lte_wifi_ignition_off.py` | WiFi after ignition off | — |
| `TC_wifi_2239` | `tests/wifi/test_tc_wifi_2239_internet_reconnection_post_wifi_disconnect_ignition_off.py` | Reconnect after ign off | `BG4-986`, `DT-3725`, `BG4-552` |

---

### Flow 9: External Camera (DHUB) WiFi Mode

**What happens:** When ext_cam is enabled, the service manages WiFi mode transitions between STA (station) and AP (access point) for DHUB connectivity. It receives `REQ_TOGGLE_DRIVERI_WIFI_MODE` messages to switch modes. DHUB connectivity is monitored via ping to hotspot_ip_address (10.42.0.x). MDVR connectivity check uses ping with response time tracking. DHUB config corruption is reported via `SM_E_WMGR_DHUB_CONF_CORRUPT`.

**When active:** When `[ext_cam_config] enabled = true`
**Frequency:** On mode switch requests from ext_cam service
**Cross-service impact:** ext_cam depends on hotspot for DHUB pairing; uploader/power_mon receive mode info

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `RECIEVED REQ_TOGGLE_DRIVERI_WIFI_MODE` | Mode switch request |
| `Going To Toggle Wifi Mode To AP` | Switching to AP mode |
| `Going To Toggle Wifi Mode To STA` | Switching to STA mode |
| `Persisting AP Mode As GEN 2 DHUB with VBUS Paired With Device` | Persistent AP mode |
| `current_wifi_mode = <N>` | Current mode logged |
| `device_wifi_mode = <N>` | Target mode logged |
| `Station Is Already Up` | Already in STA mode |
| `Auto Config SSID Is <name>` | DHUB auto-config SSID |
| `RECIEVED DHUB_AP_CONFIG_CORRUPTED` | DHUB config corruption |
| `DHUB AP Config Corrupted` | Corruption reported |
| `Not Going To Connect and Recover DHUB Currently As Installer App Is Active` | Blocked by installer |
| `VBUS Is Connected With Ip: <ip>` | VBUS accessory connected |
| `DHUB Is Connected With Ip: <ip>` | DHUB connected |
| `check mdvr connectivity cmd: <cmd>` | MDVR ping check |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_wifi_1727` | `tests/wifi/test_tc_wifi_1727_check_extcam_status.py` | External camera status | `DT-4291`, `DT-4288`, `DT-4264` |

---

### Flow 10: wlan0 Interface Monitoring & Error Reporting

**What happens:** The service periodically checks wlan0 interface availability via `ifconfig | grep wlan0`. If wlan0 is not found AND ignition is ON, it reports `SM_E_WMGR_WLAN_ERR` error to service_mon (which forwards to cloud). On bagheera2, it attempts `nmcli radio wifi on` and `rfkill unblock wifi` to recover. On all platforms, it retries `ifconfig wlan0 up` up to MAX_RETRY_COUNT (4) times.

**When active:** Always
**Frequency:** On every scan cycle
**Cross-service impact:** service_mon receives wlan error events; cloud alerts on persistent failure

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `found wlan0 interface` | wlan0 present |
| `wlan0 interface not found` | wlan0 missing (critical) |
| `bringing wifi interface up` | Attempting to bring up wlan0 |
| `cmd to bring wifi interface up failed` | Failed to bring up wlan0 |
| `Turning wifi on` | nmcli radio wifi on (bagheera2) |
| `rfkill unblock wifi` | rfkill unblock attempted |
| `cmd to turn wifi on failed` | Radio turn-on failed |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_wifi_3265` | `tests/wifi/test_tc_wifi_3265_service_status_no_network.py` | Service status with no network | — |
| `TC_wifi_3266` | `tests/wifi/test_tc_wifi_3266_service_crash_restart_validation.py` | Service crash/restart | — |

---

### Flow 11: Service Stability (Reboot, LPW, Crash Recovery)

**What happens:** wifi_mgr must remain stable across ignition cycles, low power wakeup, AWS reboots, camera crash reboots, cyclic reboots, and watchdog reboots. The service is monitored by service_mon. On restart, it re-initializes MSGQ, re-registers with speed, and resumes SSID scanning. Uptime must be >= 2 minutes to be considered stable (not crashing).

**When active:** Always
**Frequency:** Verified after every reboot/power event
**Cross-service impact:** service_mon restarts on crash; power_monitor triggers reboots

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `**********Starting WIFI MGR Service**********` | Service (re)start |
| `Message queue server created WIFI_MGR` | Reinitialized after restart |
| `Speed Service Registration Successfull` | Re-registered with speed |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_wifi_1749` | `tests/wifi/test_tc_wifi_1749_stability_lpw.py` | Stability during LPW | — |
| `TC_wifi_1750` | `tests/wifi/test_tc_wifi_1750_stability_aws_reboot.py` | Stability after AWS reboot | — |
| `TC_wifi_1751` | `tests/wifi/test_tc_wifi_1751_stability_camera_crash_reboot.py` | Stability after camera crash | — |
| `TC_wifi_1752` | `tests/wifi/test_tc_wifi_1752_stability_cyclic_reboot.py` | Stability after cyclic reboot | — |
| `TC_wifi_1753` | `tests/wifi/test_tc_wifi_1753_stability_watchdog_reboot.py` | Stability after watchdog reboot | — |
| `TC_wifi_2233` | *(no .py file — test case not yet implemented)* | Master LPM stability | — |
| `TC_wifi_3266` | `tests/wifi/test_tc_wifi_3266_service_crash_restart_validation.py` | Crash restart validation | — |
| `TC_wifi_3267` | `tests/wifi/test_tc_wifi_3267_service_restart_uptime_check.py` | Uptime check after restart | — |

---

### Flow 12: WiFi Antenna Time & Scan Trigger

**What happens:** The service can wait for antenna time before starting WiFi scans. On receiving `REQUEST_ANTENNA_TIME_RESPONSE`, it unblocks the scan thread via condition variable. This ensures WiFi scanning doesn't interfere with GPS antenna initialization on shared-antenna platforms.

**When active:** On platforms with shared WiFi/GPS antenna
**Frequency:** Once at boot
**Cross-service impact:** GPS service signals antenna readiness

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `Received REQUEST_ANTENNA_TIME_RESPONSE` | Antenna time received |
| `Start WiFi scan Signal sent` | Scan unblocked |

---

## Config-Driven Flow Activation

The agent MUST read the device config from `device_data/device_<ID>_config.ini`
before selecting test cases. Use the following mapping:

| Config Section | Config Key | Value | Activates Flow(s) | Test Cases Affected |
|---|---|---|---|---|
| `[wifi]` | `wifi_fallback` | `enable` | SSID Scanning & Connection, Ping Monitoring, Route Metric | TC_1722, TC_1725, TC_1728, TC_1733, TC_1735 |
| `[wifi]` | `count` | `>0` | SSID list (multiple SSIDs) | TC_1725 |
| `[wifi]` | `hotspot_interface` | `wlan0` | Hotspot/AP mode | TC_1727 |
| `[wifi]` | `operation_mode` | `sta`/`ap` | Default mode at boot | All |
| `[ext_cam_config]` | `enabled` | `true` | Ext Cam/DHUB WiFi mode | TC_1727 |
| `[driverlogin]` | `login_speed` | `<N>` | DTA speed threshold | Speed integration |
| — | — | — | Init, Stability, Health Stats (always active) | All non-gated TCs |

**Rules:**
- Flows marked "always active" → run their test cases unconditionally
- WiFi fallback flow → run only if `wifi_fallback = enable`
- Ext cam/DHUB flow → run only if `[ext_cam_config] enabled = true`
- DTA mode → activated via `automation_config.ini` presence with `[wifi]` section

---

## Cross-Service Dependencies

| Related Service | Why | When to check its logs |
|---|---|---|
| `service_mon` | Monitors wifi_mgr health; restarts on crash | When validating stability, error reporting |
| `power_monitor` | Sends ignition status via MSGQ | When validating ignition-dependent behavior |
| `speed` | Sends speed updates; wifi_mgr registers/unregisters | When validating DTA/scan suppression |
| `ndcentral` | Requests health stats wifi payload | When validating health payload content |
| `ext_cam` | Sends DHUB mode switch requests | When validating AP/STA mode transitions |
| `conn_mgr` | Manages LTE; wifi route metric affects LTE usage | When validating route/data connectivity |
| `uploader` | Depends on connectivity path (wifi vs LTE) | When validating upload behavior |

---

## Flow Dependency Graph

```
boot → [Flow: Init & Config Parse] → [Flow: MSGQ + Speed Registration]
                                            ↓
                                      [Flow: wlan0 Interface Check]
                                            ↓ (wlan0 up)
                                      [Flow: Antenna Time Wait] (if applicable)
                                            ↓
                                      [Flow: SSID Scan & Connect] (if wifi_fallback=enable)
                                            ↓ (connected)
                                      [Flow: Route Metric Alteration]
                                      [Flow: Ping Monitoring] (periodic)
                                            ↓ (on health request)
                                      [Flow: Health Stats WiFi Payload]
                                            ↓ (on ext_cam request)
                                      [Flow: DHUB Mode Switch (STA ↔ AP)]
                                            ↓ (on installer app)
                                      [Flow: Hotspot/AP Mode]
                                            ↓ (across power events)
                                      [Flow: Stability & Recovery]
```

---

## Device-Type Specifics

| Device Type | Platform Define | WiFi Module | Connection Method | Hotspot Method | Scan Command | Notes |
|---|---|---|---|---|---|---|
| krait (D210) | `KRAIT` | QCA | wpa_supplicant + dhcpcd | hostapd + dnsmasq | `iw dev wlan0 scan` | Route: `route del default dev wlan0` |
| krait2 (D215) | `KRAIT` | QCA | wpa_supplicant + dhcpcd | hostapd + dnsmasq | `iw dev wlan0 scan` | Route: `route del default dev wlan0` |
| bagheera2 (D410) | `BAGHEERA2` | Various | nmcli dev wifi connect | nmcli con up DriveriHostspot | `iwlist wlan0 scan` | Has WPA2 security check |
| bagheera3 (D450) | `BAGHEERA3` | Azurewave | nmcli dev wifi connect | nmcli con up DriveriHostspot | `iw dev wlan0 scan` | Standard |
| bagheera4 (D470) | `BAGHEERA4` | Azurewave | nmcli dev wifi connect | nmcli con up DriveriHostspot | `iw dev wlan0 scan` | Standard |

---

## Test Categories

| Category | TCs |
|----------|-----|
| Service Status & Init | TC_1723, TC_1724, TC_1736, TC_3263, TC_3265, TC_3268 |
| SSID Scan & Connection | TC_1722, TC_1725, TC_1728, TC_1733, TC_1735, TC_2232, TC_2235, TC_2239 |
| Ping & Internet | TC_1732, TC_1735, TC_2236 |
| Hotspot / Ext Cam | TC_1727 |
| Health Stats Payload | TC_1755, TC_1779, TC_2231, TC_2234, TC_2237, TC_2238, TC_3401 |
| Stability (Reboot/LPW) | TC_1749, TC_1750, TC_1751, TC_1752, TC_1753, TC_2233, TC_3266, TC_3267 |
| Data Connectivity | TC_1733, TC_2232, TC_2235 |

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Determine device type** — affects scan command, connection method, and hotspot approach
3. **Determine active flows** using the Config-Driven Flow Activation table above
4. **For each active flow**, read the mapped `.py` test case files from `tests/wifi/`
5. **Search device logs** in `device_logs/<device_id>/wifi_mgr.log` using patterns from this skill
6. **For cross-service checks**, also search logs of: `service_mon`, `power_monitor`, `ndcentral`
7. **For health payload validation**, check `ndcentral` logs for `get_network_info returned` and health backup files
8. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED
