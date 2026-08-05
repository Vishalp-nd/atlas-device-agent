---
name: awsiot-service-validation
description: "Use when: validating AWSIOT (nd_iot) service behavior from device logs. Covers MQTT connection, shadow sync (classic/named), keepalive ping, VOD requests, livestreaming, GPS publishing, ELD data, certificate management, key corruption detection, exponential backoff reconnection, and service stability."
argument-hint: "device serial (e.g., /awsiot-service-validation 2543fa04)"
---

# AWSIOT Service (`nd_iot`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the AWSIOT service —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads `.py` test case files from `tests/awsiot/` and
> `tests/awsiot_sdk/` for actual log patterns, device-type paths, and acceptance
> criteria — this skill does NOT duplicate those.

---

## Service Overview

`nd_iot` (AWSIOT) is a critical always-running service that manages the device's connection to AWS IoT Core via MQTT. It is the primary cloud communication channel for the device. It handles shadow synchronization (classic shadow, named shadow for livestream, named shadow for VOD), processes cloud commands (ping/keepalive, reboot, VOD requests, livestreaming), publishes GPS and ELD telemetry data, and manages device certificate lifecycle including corruption detection and recovery. The service interacts with uploader, nd-central, nd_sam, power_monitor, and service_mon via message queues.

**Process name:** `nd_iot` (binary: `AwsIotClient`)
**Log file:** `awsiot.log` (path: `/home/ubuntu/.nddevice/log/awsiot/`)
**Primary config sections:** `[awsiot]`, `[cloud]`, `[eld]`, `[gps]`
**Config files:** `cloudconfig.ini`, `bagheera_config.ini`, `bagheera_override.ini`, `deviceconfig.ini`
**Message queue name:** `AWSIOT` (server), `AWSIOT_PUB` (publish thread)
**Certificate folder:** `/home/ubuntu/.nddevice/certificate/`

---

## Service Flows

### Flow 1: Service Initialization & Configuration Parsing

**What happens:** On startup, the service initializes the logger, creates NDService object, parses `cloudconfig.ini` to get server address and injection version, reads the device's endpoint (prod/staging), loads certificate paths, creates the message queue (`AWSIOT`), and spawns the publish thread and ELD thread if enabled. It also reads override configs from `bagheera_override.ini`.

**When active:** Always (on every service start/restart)
**Frequency:** Once at boot / on service restart
**Cross-service impact:** service_mon monitors this service; other services depend on AWSIOT MSGQ being available

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `Creating NDService object: IOT` | Service object initialization |
| `##### Starting AWSIOT #####` | Service startup marker |
| `DeviceType: <type>` | Device type detected |
| `get_params: server_address - <url>` | Server URL configured |
| `get_params: pub_key_registration_url - <url>` | Public key registration URL |
| `endpoint "prod" is being changed to "production"` | Endpoint normalization |
| `Message queue server created AWSIOT` | MSGQ successfully created |
| `Message queue created` | MSGQ ready for use |
| `Override file <path> present` | Override config detected |
| `OVerride file parsed successfully` | Override config applied |
| `ELD publish thread creation failed` | ELD thread spawn failure |
| `Can't create publish thread, exiting` | Publish thread failure (fatal) |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_AWSIOT_940` | `tests/awsiot/test_TC_awsiot_940_service_status_check.py` | Service is active/running | — |
| `TC_AWSIOT_941` | `tests/awsiot/test_TC_awsiot_941_override_parse_check.py` | Override config parsed | — |
| `TC_AWSIOT_962` | `tests/awsiot/test_TC_awsiot_962_msgq_creation.py` | MSGQ creation successful | — |
| `TC_AWSIOT_964` | `tests/awsiot/test_TC_awsiot_964_verify_server_address.py` | Server address configured | — |
| `TC_AWSIOT_987` | `tests/awsiot/test_TC_awsiot_987_verify_logging.py` | Logging active | — |
| `TC_AWSIOT_2479` | `tests/awsiot/test_TC_awsiot_2479_check_binary_and_permissions.py` | Binary exists with correct permissions | — |

---

### Flow 2: MQTT Connection & Exponential Backoff

**What happens:** The service checks internet connectivity (ping 8.8.8.8, 1.1.1.1, IPv6 endpoints), loads AWS IoT certificates and private key into memory buffers (with decryption), then attempts MQTT connection to AWS IoT Core on port 8883. On failure, it uses exponential backoff with jitter (base 5s, max 7 retries). On success, it registers delta/read callbacks and initializes shadows.

**When active:** Always — after config parsing
**Frequency:** Once at boot; retries on disconnection
**Cross-service impact:** All cloud-dependent operations (VOD, ping, livestream) blocked until connected

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `Internet exists` | Connectivity check passed |
| `No internet, sleep <N> sec` | No connectivity, waiting |
| `Loading AwsIoT keys to buffer...` | Certificate loading started |
| `AwsIoT Certificate file read successfully: <N> bytes` | Cert loaded |
| `Successfully loaded AwsIoT keys to memory buffers` | Both cert+key loaded |
| `Trying to connect to AWS IOT server... Retry <N>` | Connection attempt |
| `Connected successfully` | MQTT connection established |
| `Connection failed with error <err>` | Connection failure |
| `Unable to connect to Aws IoT server` | Connection attempt failed |
| `CONNECTION_RETRY_DELAY: <N>` | Backoff delay value |
| `Disconnected!!` | MQTT disconnected |
| `Connection interrupted: <err>` | Connection lost |
| `Connection resumed` | Auto-reconnected |
| `Connecting... Thing: <name>` | Connection in progress |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_AWSIOT_989` | `tests/awsiot/test_TC_awsiot_989_server_connected_or_not.py` | Server connection status | — |
| `TC_AWSIOT_975` | `tests/awsiot/test_TC_awsiot_975_check_certificates.py` | Certificates valid | — |
| `TC_AWSIOT_988` | `tests/awsiot/test_TC_awsiot_988_delay_connmgr_verify_awsiot_behaviour.py` | Backoff behavior | — |
| `TC_AWSIOT_1145` | `tests/awsiot/test_TC_awsiot_1145_verify_connection_ignition_high.py` | Connection during ignition high | `DT-4106` |
| `TC_AWSIOT_1146` | *(no .py file — test case not yet implemented)* | Connection during low power wakeup | `DT-4106` |
| `TC_AWSIOT_1147` | `tests/awsiot/test_TC_awsiot_1147_verify_connection_no_network.py` | Behavior when no network | `DT-4106` |
| `TC_AWSIOT_2212` | `tests/awsiot/test_TC_awsiot_2212_verify_iot_priv_key_decryption_before_connect.py` | Key decryption before connect | — |

---

### Flow 3: Shadow Sync (Classic + Named Shadows)

**What happens:** After MQTT connect, the service initializes three shadow objects: Classic Shadow (device state, cameras, auth_method, vehicleClass, counter), Named Shadow "priority-shadow-ls" (livestream requests), Named Shadow "priority-shadow-vod" (VOD requests). It reads shadow state, processes deltas, and reports back to cloud. Shadow updates are queued and processed sequentially. A counter sync with nd_sam is performed on connect.

**When active:** Always when connected
**Frequency:** On connect (initial read), on delta (cloud-pushed changes), periodic counter sync
**Cross-service impact:** nd_sam sends counter sync via MSGQ; VOD and livestream requests arrive via named shadows

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `read_cb :: shadow_type: 0` | Classic shadow read received |
| `read_cb :: shadow_type: 1` | Named LS shadow read received |
| `read_cb :: shadow_type: 2` | Named VOD shadow read received |
| `read_update :: Classic Shadow : <json>` | Classic shadow content |
| `read_update :: priority-shadow-ls : <json>` | LS shadow content |
| `read_update :: priority-shadow-vod : <json>` | VOD shadow content |
| `delta_cb :: shadow_type: <N>` | Delta (change) received |
| `Processing shadow update(s): <N>` | Processing queued updates |
| `Shadow update(s) processed` | All updates processed |
| `Entered sync_counter_with_shadow` | Counter sync started |
| `Updating shadow with pass counter: <json>` | Counter being synced |
| `Counter synced with shadow` | Counter sync successful |
| `Unable to sync counter` | Counter sync failed |
| `Successfully updated shadow state.` | Shadow update published |
| `update_shadow::<shadow_name> <<json>>` | Shadow update being sent |
| `Shadow full detected: <type>` | Shadow capacity exceeded (critical) |
| `Shadow sync failed consecutively` | Repeated sync failures |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_AWSIOT_SDK_2094` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2094_validate_classic_shadow.py` | Classic shadow valid | — |
| `TC_AWSIOT_SDK_2095` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2095_validate_livestream_shadow.py` | LS named shadow valid | — |
| `TC_AWSIOT_SDK_2096` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2096_validate_vod_shadow.py` | VOD named shadow valid | — |
| `TC_AWSIOT_SDK_2120` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2120_validate_nd_sam_counter.py` | nd_sam counter synced | — |
| `TC_AWSIOT_965` | `tests/awsiot/test_TC_awsiot_965_all_cameras_enabled.py` | Cameras reported in shadow | — |
| `TC_AWSIOT_961` | `tests/awsiot/test_TC_awsiot_961_verify_vehicle_class.py` | Vehicle class in shadow | — |
| `TC_AWSIOT_SDK_2104` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2104_validate_cameras_enabled.py` | Camera count in shadow | — |
| `TC_AWSIOT_SDK_2112` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2112_validate_vehicle_details.py` | Vehicle details reported | — |

---

### Flow 4: Keepalive / Ping Requests

**What happens:** Cloud sends ping requests via shadow delta. Types include: keepalive (health check), health_stats (report stats), screenshot, kill_app_process, list_all_processes, clear_space, reboot_phone, inward_camera. The service acknowledges (RECV→ACK→DONE/ERR), processes the command, and reports status back to shadow.

**When active:** Always when connected
**Frequency:** On-demand from cloud
**Cross-service impact:** Reboot requests sent to power_monitor; some pings interact with nd-central

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `send_keepalive` | Keepalive ping processed |
| `reboot` | Reboot command received |
| `send_cloud_recv` | Request acknowledged as received |
| `send_cloud_msg` | Status update sent to cloud |
| `do_ping` | Ping request being processed |
| `type: ping, status: recv` | Ping acknowledged |
| `type: ping, status: done` | Ping completed |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_AWSIOT_1031` | `tests/awsiot/test_TC_awsiot_1031_ping_request_keep_alive.py` | Keepalive processed | — |
| `TC_AWSIOT_1032` | `tests/awsiot/test_TC_awsiot_1032_ping_request_health_stats.py` | Health stats ping | — |
| `TC_AWSIOT_1033` | `tests/awsiot/test_TC_awsiot_1033_ping_request_screenshot.py` | Screenshot ping | — |
| `TC_AWSIOT_1034` | `tests/awsiot/test_TC_awsiot_1034_ping_request_kill_app_process.py` | Kill process ping | — |
| `TC_AWSIOT_1035` | `tests/awsiot/test_TC_awsiot_1035_ping_request_list_all_processes.py` | List processes ping | — |
| `TC_AWSIOT_1036` | `tests/awsiot/test_TC_awsiot_1036_ping_request_clear_space.py` | Clear space ping | — |
| `TC_AWSIOT_1069` | `tests/awsiot/test_TC_awsiot_1069_ping_request_reboot_phone.py` | Reboot phone ping | — |
| `TC_AWSIOT_1006` | `tests/awsiot/test_TC_awsiot_1006_ping_internal_camera.py` | Internal camera ping | — |
| `TC_AWSIOT_SDK_2074` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2074_ping_keep_alive_shadow.py` | Keepalive via shadow | — |
| `TC_AWSIOT_SDK_2083` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2083_ping_health_stats_shadow.py` | Health stats via shadow | — |
| `TC_AWSIOT_SDK_2087` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2087_ping_screenshot_shadow.py` | Screenshot via shadow | — |
| `TC_AWSIOT_SDK_2088` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2088_ping_list_all_processes_shadow.py` | List processes via shadow | — |
| `TC_AWSIOT_SDK_2089` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2089_ping_kill_app_process_shadow.py` | Kill process via shadow | — |
| `TC_AWSIOT_SDK_2100` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2100_ping_inward_camera_shadow.py` | Inward camera via shadow | — |
| `TC_AWSIOT_SDK_2101` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2101_ping_clear_space_shadow.py` | Clear space via shadow | — |
| `TC_AWSIOT_SDK_2102` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2102_ping_reboot_phone_shadow.py` | Reboot via shadow | — |

---

### Flow 5: Reboot Handling

**What happens:** When a reboot ping is received, the service sends a reboot command to power_monitor via MSGQ. The power_monitor then triggers the actual reboot. Post-reboot, service should reconnect and continue normal operation.

**When active:** On cloud-triggered reboot command
**Frequency:** On-demand
**Cross-service impact:** power_monitor executes reboot; all services restart

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `reboot` | Reboot command received from cloud |
| `Sending reboot request to powermon` | Reboot forwarded |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_AWSIOT_1070` | `tests/awsiot/test_TC_awsiot_1070_reboot_request_to_powermon.py` | Reboot sent to power_monitor | `BG4-894` |
| `TC_AWSIOT_1071` | `tests/awsiot/test_TC_awsiot_1071_verify_powermon_stability_post_aws_reboot.py` | Stability post-reboot | `BG4-894` |
| `TC_AWSIOT_1072` | `tests/awsiot/test_TC_awsiot_1072_verify_reboot_on_aws_reboot.py` | Device actually reboots | `BG4-894` |
| `TC_AWSIOT_1148` | `tests/awsiot/test_TC_awsiot_1148_reboot_keep_alive.py` | Keepalive after reboot | — |
| `TC_AWSIOT_1149` | `tests/awsiot/test_TC_awsiot_1149_api_call_versioncheck_post_reboot.py` | Version check API post reboot | — |

---

### Flow 6: VOD (Video on Demand) Requests

**What happens:** VOD requests arrive via Named Shadow "priority-shadow-vod". The service parses the request (catalog_id, start/end time, priority, trim flag), acknowledges it (RECV), then sends the upload request to the uploader service. On completion, it reports DONE/ERR to shadow. VOD count tracking is maintained.

**When active:** When connected and VOD request received
**Frequency:** On-demand from cloud
**Cross-service impact:** Sends upload request to uploader service; uploader sends RES_UPLOAD_VOD back

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `do_vod` | VOD request processing |
| `type: vod, status: recv` | VOD acknowledged |
| `type: vod, status: done` | VOD upload completed |
| `type: vod, status: err` | VOD upload failed |
| `send_hs_vod_status` | VOD status sent to health stats |
| `Received UPDATE_DEVICE_VOD_COUNT` | VOD counter updated |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_AWSIOT_1108` | `tests/awsiot/test_TC_awsiot_1108_receive_video_request.py` | VOD request received | `IDMS-50792` |
| `TC_AWSIOT_1109` | `tests/awsiot/test_TC_awsiot_1109_send_request_to_uploader.py` | Request sent to uploader | `IDMS-50792` |
| `TC_AWSIOT_1110` | `tests/awsiot/test_TC_awsiot_1110_state_change_to_upload_state.py` | VOD state transitions | `IDMS-50792` |
| `TC_AWSIOT_1112` | `tests/awsiot/test_TC_awsiot_1112_verify_payload.py` | VOD payload correct | `IDMS-50792` |
| `TC_AWSIOT_SDK_2052` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2052_vod_upload_priority_6.py` | VOD priority handling | `IDMS-50792` |

---

### Flow 7: Livestreaming

**What happens:** Livestream requests arrive via Named Shadow "priority-shadow-ls". The service handles outward, inward, and dual livestream modes. It sends the request to nd-central for camera stream initialization. A timer governs stream duration. Audio can be enabled/disabled per config. When the timer elapses or stop is received, stream is terminated.

**When active:** When connected and livestream requested
**Frequency:** On-demand from cloud
**Cross-service impact:** nd-central manages actual camera streams; Kinesis video streams used for transport

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `do_livestream` | Livestream request processing |
| `do_dual_livestream` | Dual livestream request processing |
| `type: livestream, status: recv` | Livestream acknowledged |
| `type: livestream, status: done` | Livestream ended |
| `type: dual_livestream` | Dual stream request |
| `Livestream timer elapsed` | Stream duration expired |
| `Kinesis stream stop` | Stream stopped |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_AWSIOT_1007` | `tests/awsiot/test_TC_awsiot_1007_livestreaming_default_config.py` | Default livestream config | — |
| `TC_AWSIOT_1012` | `tests/awsiot/test_TC_awsiot_1012_verify_request_id_assign_livestream.py` | Request ID assigned | — |
| `TC_AWSIOT_1107` | `tests/awsiot/test_TC_awsiot_1107_livestream_request_to_ndcentral.py` | Request forwarded to nd-central | — |
| `TC_AWSIOT_1153` | `tests/awsiot/test_TC_awsiot_1153_outward_camera_livestream.py` | Outward camera stream | `DT-3822`, `DT-3799`, `BG4-785` |
| `TC_AWSIOT_1154` | `tests/awsiot/test_TC_awsiot_1154_inward_camera_livestream.py` | Inward camera stream | `DT-3822`, `DT-3799`, `BG4-785` |
| `TC_AWSIOT_1165` | `tests/awsiot/test_TC_awsiot_1165_live_streaming_timer.py` | Stream timer behavior | `DT-3822`, `BG4-785` |
| `TC_AWSIOT_1171` | `tests/awsiot/test_TC_awsiot_1171_kinesis_stream_stop.py` | Kinesis stream stop | — |
| `TC_AWSIOT_1172` | `tests/awsiot/test_TC_awsiot_1172_message_received_from_ndcentral.py` | nd-central response | — |
| `TC_AWSIOT_1175` | `tests/awsiot/test_TC_awsiot_1175_stream_timer_elapsed.py` | Timer expiry | — |
| `TC_AWSIOT_1181` | `tests/awsiot/test_TC_awsiot_1181_outward_livestream_config_disabled.py` | Outward disabled | — |
| `TC_AWSIOT_1185` | `tests/awsiot/test_TC_awsiot_1185_inward_livestream_config_disabled.py` | Inward disabled | — |
| `TC_AWSIOT_1815` | `tests/awsiot/test_TC_awsiot_1815_inward_livestream_audio_enabled.py` | Inward audio ON | — |
| `TC_AWSIOT_1816` | `tests/awsiot/test_TC_awsiot_1816_inward_livestream_audio_disabled.py` | Inward audio OFF | — |
| `TC_AWSIOT_1817` | `tests/awsiot/test_TC_awsiot_1817_outward_livestream_audio_enabled.py` | Outward audio ON | — |
| `TC_AWSIOT_1818` | `tests/awsiot/test_TC_awsiot_1818_outward_livestream_audio_disabled.py` | Outward audio OFF | — |
| `TC_AWSIOT_1819` | `tests/awsiot/test_TC_awsiot_1819_dual_livestream_audio_enabled.py` | Dual audio ON | — |
| `TC_AWSIOT_1820` | `tests/awsiot/test_TC_awsiot_1820_dual_livestream_audio_disabled.py` | Dual audio OFF | — |
| `TC_AWSIOT_2806` | `tests/awsiot/test_TC_awsiot_2806_check_livestreaming_when_device_offline.py` | Livestream offline | — |
| `TC_AWSIOT_SDK_2121` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2121_outward_cam_streaming_audio.py` | Outward cam stream audio | — |
| `TC_AWSIOT_SDK_2130` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2130_inward_cam_streaming_audio.py` | Inward cam stream audio | — |

---

### Flow 8: GPS Data Publishing

**What happens:** If `send_gps_updates_to_awsiot` is enabled in config, the service subscribes to GPS data via NDMB (message bus). It receives GPS lat/long/speed data and publishes it to AWS IoT MQTT topic at configured frequency. GPS speed threshold is 5 km/h (publishes only when moving above threshold or on change).

**When active:** Only when `gps_tracking_enabled = true` in `[awsiot]` config
**Frequency:** Every 10 seconds (configurable)
**Cross-service impact:** Depends on GPS service for data; publishes to cloud

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `subscribe for GPS data` | GPS subscription initiated |
| `subscribed for GPS data` | GPS subscription successful |
| `send_gps_updates_to_awsiot is false` | GPS publish disabled |
| `aws iot publish is not enabled in config file` | Publish feature disabled |
| `publishing gps data` | GPS data being published |
| `Published data successfully to topic <topic>` | GPS publish success |
| `No internet connection, skipping publish` | Publish skipped (offline) |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_AWSIOT_953` | `tests/awsiot/test_TC_awsiot_953_verify_publish_enabled.py` | Publish feature enabled | — |
| `TC_AWSIOT_954` | `tests/awsiot/test_TC_awsiot_954_verify_gps_publish_frequency.py` | GPS publish frequency | — |
| `TC_AWSIOT_1333` | `tests/awsiot/test_TC_awsiot_1333_publish_gps_lat_long.py` | GPS lat/long published | — |
| `TC_AWSIOT_2557` | `tests/awsiot/test_TC_awsiot_2557_publishes_gps_data_every_10_secs.py` | 10-sec publish interval | — |

---

### Flow 9: Certificate & Key Management

**What happens:** The service manages three key files: `certificate.pem.crt` (AWS IoT cert), `private.pem.key` (AWS IoT private key, encrypted), `ed25519key.pem` (JWT key for public key auth). On connection failure with `NOT_AUTHORIZED` or `UNEXPECTED_HANGUP`, it deletes certificates and exits (service_mon will restart it). Key corruption is checked every 3 minutes. Public key registration is attempted if keys are not registered.

**When active:** Always — key integrity monitored during operation
**Frequency:** Key corruption check every 3 minutes; cert validation on connect
**Cross-service impact:** service_mon restarts on cert-error exit; cloud re-provisions certificates

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `AWSIoT cert error, Exiting...` | Certificate auth failed (fatal) |
| `delete_certificate_files()` | Certs being deleted for re-provision |
| `Key corruption detected` | Private key corruption found |
| `Key is not yet registered. Registering...` | Public key registration needed |
| `check_for_key_corruption` | Periodic key check running |
| `Auth key corruption detected` | Critical alert raised |
| `Failed to read AwsIoT certificate or private key` | Cert/key load failure |
| `Certificate file could not be opened` | Cert file missing/unreadable |
| `Certificate file is empty` | Cert file has zero bytes |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_AWSIOT_975` | `tests/awsiot/test_TC_awsiot_975_check_certificates.py` | Certificates exist and valid | — |
| `TC_AWSIOT_991` | `tests/awsiot/test_TC_awsiot_991_certificate_pem_corruption_handling.py` | Cert corruption recovery | — |
| `TC_AWSIOT_992` | `tests/awsiot/test_TC_awsiot_992_private_pem_corruption_handling.py` | Private key corruption recovery | — |
| `TC_AWSIOT_998` | `tests/awsiot/test_TC_awsiot_998_service_exiting_when_bad_certificates_found.py` | Exit on bad certs | — |
| `TC_AWSIOT_1005` | `tests/awsiot/test_TC_awsiot_1005_jwt_registration_when_corrupted.py` | JWT key re-registration | — |
| `TC_AWSIOT_1023` | `tests/awsiot/test_TC_awsiot_1023_verify_ka_certificate_check.py` | KA certificate check | — |
| `TC_AWSIOT_2212` | `tests/awsiot/test_TC_awsiot_2212_verify_iot_priv_key_decryption_before_connect.py` | Key decryption works | — |
| `TC_AWSIOT_2249` | `tests/awsiot/test_TC_awsiot_2249_verify_private_awsiot_key_encryption_on_regeneration.py` | Key re-encryption | — |
| `TC_AWSIOT_2360` | `tests/awsiot/test_TC_awsiot_2360_verify_private_jwt_key_encryption_on_regeneration.py` | JWT key encryption | — |
| `TC_AWSIOT_2364` | `tests/awsiot/test_TC_awsiot_2364_verify_backup_corruption_handling.py` | Backup corruption handling | — |
| `TC_AWSIOT_2559` | `tests/awsiot/test_TC_awsiot_2559_check_encryption_and_permission_of_private_keys.py` | Key permissions | — |
| `TC_AWSIOT_SDK_2111` | `tests/awsiot_sdk/test_TC_awsiot_sdk_2111_validate_auth_method.py` | Auth method in shadow | — |

---

### Flow 10: ELD (Electronic Logging Device) Data

**What happens:** If `eld_enabled = true` in config, the service spawns an ELD publish thread that reads CAN bus data (via nd_mbclient) and publishes it to a dedicated MQTT topic. QoS level is configurable (default QoS 1). This is specific to krait, krait2, and bagheera2 platforms.

**When active:** Only when `[eld] eld_enabled = true`
**Frequency:** Continuous when enabled
**Cross-service impact:** Depends on CAN bus / OBD data from vehicle

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `MQTT QoS level configured for ELD is <N>` | ELD QoS level set |
| `No value present for key eld_enabled` | ELD not configured (disabled) |
| `ELD publish thread creation failed` | ELD thread spawn failure |

**Test cases that validate this flow:**
- ELD-specific test cases are in the general awsiot test suite when device has ELD enabled.

---

### Flow 11: Service Stability & Error Recovery

**What happens:** The service must remain stable across ignition cycles (ignition off → low power wakeup → ignition on), abrupt power-off events, and network disruptions. Shadow sync retry logic (max 6 retries) handles transient failures. The service raises SM_E_AWS_SHADOW_SYNC_FAILED alert after max retries. On yield failures (>15), the service disconnects and exits.

**When active:** Always
**Frequency:** Continuous
**Cross-service impact:** service_mon restarts on unexpected exit

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `Exiting AWSIoT! Disconnection status: <N>` | Graceful exit |
| `Exiting AWSIOT, msg_loop_ret: <N>` | Message loop exit |
| `Will retry shadow sync` | Retry scheduled |
| `Not retrying shadow sync as failing consecutively` | Max retries exceeded |
| `Raising critical alert for shadow sync failure` | Critical alert |
| `AWSIoT connectivity status: <N>` | Connectivity state change |
| `setting msg_loop_active flag` | MSGQ loop started |
| `clearing msg_loop_active flag` | MSGQ loop stopped |
| `Uploader is awake` | Uploader service available |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_AWSIOT_963` | `tests/awsiot/test_TC_awsiot_963_service_stability.py` | General stability | `DT-3133` |
| `TC_AWSIOT_993` | `tests/awsiot/test_TC_awsiot_993_stability_ignition_off_awsiot_reboot.py` | Stability: ign off + reboot | `DT-3133` |
| `TC_AWSIOT_994` | `tests/awsiot/test_TC_awsiot_994_stability_lowpowerwakeup_awsiot_reboot.py` | Stability: LPW + reboot | — |
| `TC_AWSIOT_1021` | `tests/awsiot/test_TC_awsiot_1021_stability_ignition_off_abruptpoweroff.py` | Stability: ign off + abrupt | — |
| `TC_AWSIOT_1022` | *(no .py file — test case not yet implemented)* | Stability: LPW + abrupt | — |

---

### Flow 12: Cloud Status Reporting & Misc Requests

**What happens:** The service reports its status (auth_method, private_key_status, cameras count, vehicleClass, vehicleDetailHash) to cloud via shadow. Misc requests like `certificate-check-disabled-on-keep-alive-api`, `config_validation`, and `data_recording_status` are handled via the misc_request_list. The service also sends eventdata API calls and version check API calls.

**When active:** After shadow sync
**Frequency:** On connect and on cloud request
**Cross-service impact:** Reports status for cloud dashboards; data recording status forwarded to nd-central

**Key log patterns:**
| Pattern | Meaning |
|---------|---------|
| `send_response: {"state": {"reported": {"cameras": <N>}}}` | Camera count reported |
| `send_response: {"state": {"reported": {"auth_method": "<m>"}}}` | Auth method reported |
| `send_response: {"state": {"reported": {"private_key_status": "<s>"}}}` | Key status reported |
| `send_response: {"state": {"reported": {"vehicleClass": "<c>"}}}` | Vehicle class reported |
| `parse_misc_req_for_key:: certificate-check-disabled-on-keep-alive-api` | KA cert check parsed |

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_AWSIOT_1017` | `tests/awsiot/test_TC_awsiot_1017_sent_status_to_cloud.py` | Status sent to cloud | — |
| `TC_AWSIOT_997` | `tests/awsiot/test_TC_awsiot_997_verify_vehicledetailhash.py` | Vehicle detail hash | — |
| `TC_AWSIOT_1105` | `tests/awsiot/test_TC_awsiot_1105_eventdata_api_call.py` | Eventdata API call | — |
| `TC_AWSIOT_1106` | `tests/awsiot/test_TC_awsiot_1106_eventdata_api_response.py` | Eventdata API response | — |

---

## Config-Driven Flow Activation

The agent MUST read the device config from `device_data/device_<ID>_config.ini`
before selecting test cases. Use the following mapping:

| Config Section | Config Key | Value | Activates Flow(s) | Test Cases Affected |
|---|---|---|---|
| `[awsiot]` | `enabled` | `true` | All flows | All TCs |
| `[awsiot]` | `gps_tracking_enabled` | `true` | GPS Data Publishing | `TC_AWSIOT_953`, `TC_AWSIOT_954`, `TC_AWSIOT_1333`, `TC_AWSIOT_2557` |
| `[eld]` | `eld_enabled` | `true` | ELD Data | ELD-related TCs |
| `[awsiot]` | `publish_enabled` | `true` | GPS Data Publishing (MQTT topic) | `TC_AWSIOT_953` |
| — | — | — | Init, Connection, Shadow, Keepalive, Stability (always active) | All non-gated TCs |

**Rules:**
- Flows marked "always active" → run their test cases unconditionally
- Flows gated by a config key → run only if the device config has that key set to the activating value
- If the config key is missing from the device config → assume disabled (default)
- GPS publish and ELD are platform-dependent (ELD only on krait/krait2/bagheera2)

---

## Cross-Service Dependencies

| Related Service | Why | When to check its logs |
|---|---|
| `service_mon` | Monitors awsiot health; restarts on crash/exit | When validating stability, cert error exits |
| `power_monitor` | Receives reboot commands from awsiot | When validating reboot flow |
| `uploader` | Receives VOD upload requests; sends completion status back | When validating VOD flow |
| `nd-central` | Receives livestream requests; sends stream status back | When validating livestream flow |
| `nd_sam` | Sends counter sync messages to awsiot | When validating counter/shadow sync |
| `gps` | Provides GPS data via message bus (NDMB) | When validating GPS publishing |
| `circular_buffer` | Receives data recording status from awsiot | When validating data recording |

---

## Flow Dependency Graph

```
boot → [Flow: Init & Config Parse] → [Flow: MQTT Connection (with backoff)]
                                            ↓ (connected)
                                      [Flow: Shadow Sync (classic + named)]
                                            ↓
                                      [Flow: Keepalive/Ping] ← cloud requests
                                      [Flow: VOD Requests] ← named shadow VOD delta
                                      [Flow: Livestreaming] ← named shadow LS delta
                                      [Flow: GPS Publishing] (if gps_tracking_enabled)
                                      [Flow: ELD Data] (if eld_enabled, krait/bagheera2 only)
                                      [Flow: Status Reporting] → shadow updates
                                            ↓ (periodic)
                                      [Flow: Key Corruption Check] every 3 min
                                            ↓ (on error)
                                      [Flow: Stability & Recovery] → exit/restart
```

---

## Device-Type Specifics

| Device Type | Platform Define | ELD Support | Certificate Path | Notes |
|---|---|---|---|
| bagheera3 (D450) | `BAGHEERA3` | No | `/home/ubuntu/.nddevice/certificate/` | Standard path |
| bagheera2 (D410) | `BAGHEERA2` | Yes | `/home/ubuntu/.nddevice/certificate/` | Has ELD, cam_override at `/home/ubuntu/config/` |
| krait (D210) | `KRAIT` | Yes | `/home/ubuntu/.nddevice/certificate/` | Has ELD, cam_override at `/data/nd_files/config/` |
| krait2 (D215) | `KRAIT2` | Yes | `/home/ubuntu/.nddevice/certificate/` | Has ELD, cam_override at `/data/nd_files/config/` |

---

## Test Categories

| Category | TCs |
|----------|-----|
| Service Status & Init | TC_940, TC_941, TC_962, TC_964, TC_987, TC_2479 |
| Connection & Certificates | TC_975, TC_989, TC_988, TC_991, TC_992, TC_998, TC_1005, TC_1023, TC_1145, TC_1146, TC_1147, TC_2212, TC_2249, TC_2360, TC_2364, TC_2559 |
| Shadow Sync | TC_961, TC_965, TC_997, TC_SDK_2094, TC_SDK_2095, TC_SDK_2096, TC_SDK_2104, TC_SDK_2111, TC_SDK_2112, TC_SDK_2120 |
| Ping/Keepalive | TC_1006, TC_1031-TC_1036, TC_1069, TC_SDK_2074, TC_SDK_2083, TC_SDK_2087-TC_SDK_2089, TC_SDK_2100-TC_SDK_2102 |
| Reboot | TC_1070, TC_1071, TC_1072, TC_1148, TC_1149 |
| VOD | TC_1108, TC_1109, TC_1110, TC_1112, TC_SDK_2052 |
| Livestreaming | TC_1007, TC_1012, TC_1107, TC_1153, TC_1154, TC_1165, TC_1171, TC_1172, TC_1175, TC_1181, TC_1185, TC_1815-TC_1820, TC_2806, TC_SDK_2121, TC_SDK_2130 |
| GPS Publishing | TC_953, TC_954, TC_1333, TC_2557 |
| Stability | TC_963, TC_993, TC_994, TC_1021, TC_1022 |
| Cloud Status | TC_1017, TC_1105, TC_1106 |
| SDK/Libraries | TC_SDK_2069, TC_SDK_2072 |

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Determine device type** — affects ELD eligibility and cert paths
3. **Determine active flows** using the Config-Driven Flow Activation table above
4. **For each active flow**, read the mapped `.py` test case files from `tests/awsiot/` and `tests/awsiot_sdk/`
5. **Search device logs** in `device_logs/<device_id>/awsiot.log` using patterns from this skill
6. **For cross-service checks**, also search logs of: `service_mon`, `power_monitor`, `uploader`, `nd-central`
7. **Check Python wrapper logs** — look for `aws_iot_utils - INFO - ::====================::AwsIotWrapper Starting::====================::` at service start and `Found root certificate ...` + `Found root keys and certificates ...` for cert validation
8. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED
