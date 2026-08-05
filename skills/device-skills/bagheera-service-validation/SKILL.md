---
name: bagheera-service-validation
description: "Use when: validating bagheera / ndcentral (ndcentral.service) service behavior from device logs. Covers initialization, session management, video recording, LD file generation, xattr metadata, DMS integration, privacy mode, partial file recovery, camera crash handling, and IMU/streaming."
argument-hint: "device ID (e.g., /bagheera-service-validation 103452403525)"
---

# bagheera / ndcentral (`ndcentral.service`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the service —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads Python test cases for actual log patterns,
> device-type paths, and acceptance criteria — this skill does NOT duplicate those.

---

## Service Overview

`ndcentral` (also called **bagheera** service) is the central coordination service for video recording on ND devices (D210, D450, D470).
It manages session lifecycle, file routing to SD card and circular buffer, xattr metadata stamping, privacy state, DMS camera coordination, IMU sensor setup, and IPC with `cam_rec`, `circ_buff`, `bagheera`, and `audio` services.
The service reads `bagheera_override.ini` at startup, creates/maintains SQLite databases (GENPROP, CAMERA_CRASH_DB, SIDE_CAM_CRASH_INFO), spawns a `copy_or_move_files` thread, and listens on POSIX message queues (`q_nd_central`, `q_nd_central_cmf`).
It interacts with `cam_rec` via session filename negotiation (`START_NEXT_SESSION`), with `circ_buff` for upload queuing, and with `bagheera` for restart/alert event routing.

**Process name:** `ndcentral`
**Log file:** `ndcentral/log_*.log`
**Log path (all device types):** `/home/ubuntu/.nddevice/log/ndcentral/log_*.log`
**Primary config sections:** `[camera]`, `[privacy_mode]`, `[privacy_mode_activate]`, `[privacy_mode_deactivate]`, `[sdcard]`, `[upload_video]`, `[power]`, `[outwardcam_streaming]`, `[inwardcam_streaming]`, `[drowsy]`

---

## Service Flows

### Flow 1: Service Initialization & Config Loading

**What happens:** On startup ndcentral reads `bagheera_override.ini`, prints device type, clears the POSIX message queues, writes kernel dirty-writeback tuning params, checks free space on EMMC, creates the reboot token file, creates or validates the GENPROP / CAMERA_CRASH_DB / SIDE_CAM_CRASH_INFO SQLite tables, reads last known GPS coordinates, resolves privacy configuration, and logs a `#### Starting: ND Central ####` critical banner.
**When active:** Always — every service start
**Frequency:** Once per boot / service restart
**Cross-service impact:** Outputs session count (`ctx.sessionCount_string`) used by all file naming; failure here prevents session creation

**Test cases that validate this flow:**
| Test Case ID        | Python Path                                                                                | What it checks                                        | Related Bugs |
| ------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------- | --- |
| `TC_bagheera_160`   | `tests/bagheera/test_tc_bagheera_160_verify_db_creation.py`                               | `success in create_table_db` appears in ndcentral log | — |
| `TC_bagheera_163`   | `tests/bagheera/test_tc_bagheera_163_verify_shared_memory_creation.py`                    | Shared memory (`/dev/shm/`) created at boot           | — |
| `TC_bagheera_164`   | `tests/bagheera/test_tc_bagheera_164_verify_imu_enabled.py`                               | `Enabling IMU sensor` logged after startup            | — |
| `TC_bagheera_2606`  | `tests/bagheera/test_tc_bagheera_2606_validate_gen_property_db.py`                        | GENPROP DB table exists and is writable               | `DT-3601` |
| `TC_bagheera_2608`  | `tests/bagheera/test_tc_bagheera_2608_validate_camera_crash_db.py`                        | CAMERA_CRASH_DB table created; crash counts reset     | `OCTO-2274`, `OCTO-2191`, `OCTO-2129` |
| `TC_bagheera_2838`  | `tests/bagheera/test_tc_bagheera_2838_no_fallback_invalid_config_override.py`             | Invalid override key does NOT fall back silently      | `BG4-514` |

---

### Flow 2: Session Management & Video File Lifecycle

**What happens:** ndcentral negotiates a new session filename with `cam_rec` every ~60 seconds by sending `START_NEXT_SESSION` with a filename of the form `_trip<id>_part<id>_<lat>_<lon>_0.0_<epoch>_y.mp4`. When a session ends, it routes the completed file (CSV metadata + video) to SD card via the `copy_or_move_files` thread, adds it to `circ_buff` via `ADD_FILE_DB`, and stamps xattr metadata if enabled. The `START_META` / `STOP_DUMP_META` messages track per-camera metadata recording windows.
**When active:** Always while recording is active
**Frequency:** New session ~every 60 seconds; file add events on each completed session
**Cross-service impact:** `cam_rec` receives session filenames; `circ_buff` queues files for upload; `audio` service writes PCM in parallel

**Test cases that validate this flow:**
| Test Case ID        | Python Path                                                                                | What it checks                                        | Related Bugs |
| ------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------- | --- |
| `TC_bagheera_089`   | `tests/bagheera/test_tc_bagheera_089_create_session_every_min.py`                         | New session created every ~60s                        | `MOW-724` |
| `TC_bagheera_150`   | `tests/bagheera/test_tc_bagheera_150_verify_start_meta.py`                                | `START_META received for cam_num N` in ndcentral log  | `DT-3749`, `DT-3495` |
| `TC_bagheera_151`   | `tests/bagheera/test_tc_bagheera_151_verify_stop_dump_meta.py`                            | `STOP_DUMP_META received for cam_num N` logged        | `DT-3607` |
| `TC_bagheera_168`   | `tests/bagheera/test_tc_bagheera_168_verify_video_file_name.py`                           | Video filename matches expected naming convention     | — |
| `TC_bagheera_1884`  | `tests/bagheera/test_tc_bagheera_1884_gps_connection_session_data.py`                     | GPS coordinates embedded in session filename          | — |
| `TC_bagheera_3380`  | `tests/bagheera/test_tc_bagheera_3380_metadata_creation_validation.py`                    | Metadata CSV created and populated correctly          | `DT-3749` |
| `TC_bagheera_143`   | `tests/bagheera/test_tc_bagheera_143_metadata_user_alert.py`                              | Metadata reflects user-alert event in session         | — |

---

### Flow 3: Video Recording & Camera Pipeline

**What happens:** ndcentral starts the camera pipeline by sending `START_CAMERA`. It monitors camera state via `RECORD_START` / `RECORD_STOP` messages per cam_num. The service tracks per-camera crash counts (max 5) and triggers `###RESTART_CAMERA###` on persistent crashes. For all enabled cameras, both HQ (`.mp4`) and LD (`.mp4.ld.mp4`) files are generated per session when LD is enabled.
**When active:** Always when ignition is on and privacy is not fully blocking recording
**Frequency:** Continuous; camera restarts on crash detection
**Cross-service impact:** `cam_rec` does the actual recording; ndcentral acts as session controller

**Test cases that validate this flow:**
| Test Case ID        | Python Path                                                                                | What it checks                                        | Related Bugs |
| ------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------- | --- |
| `TC_bagheera_034`   | `tests/bagheera/test_tc_bagheera_034_verify_video_file_gen.py`                            | Video files generated; `ctx.save_inward_video_file 0` | `OCTO-2274`, `OCTO-2191`, `OCTO-2125`, `DT-3565`, `BG4-628` |
| `TC_bagheera_035`   | `tests/bagheera/test_tc_bagheera_035_verify_video_file_move_to_sdcard.py`                 | Completed files moved to SD card folder               | — |
| `TC_bagheera_036`   | `tests/bagheera/test_tc_bagheera_036_verify_db_updated_correctly.py`                      | `ADD_FILE_DB` entry in ndcentral log per file         | — |
| `TC_bagheera_037`   | `tests/bagheera/test_tc_bagheera_037_verify_file_list_uploaded_to_cloud.py`               | `Below is the added file list` in ndcentral log       | `BG4-519` |
| `TC_bagheera_141`   | `tests/bagheera/test_tc_bagheera_141_verify_encryption_video_obs_file.py`                 | `video_encryption is enabled` at startup              | — |
| `TC_bagheera_142`   | `tests/bagheera/test_tc_bagheera_142_hq_lq_video_file_generation.py`                     | Both `.mp4` and `.mp4.ld.mp4` present per session     | `OCTO-2274`, `OCTO-2125`, `BG4-985` |
| `TC_bagheera_147`   | `tests/bagheera/test_tc_bagheera_147_start_camera.py`                                     | `START_CAMERA received` in ndcentral log              | `OCTO-2274`, `OCTO-2125`, `DT-3565` |
| `TC_bagheera_148`   | `tests/bagheera/test_tc_bagheera_148_verify_restart_camera_message.py`                    | `###RESTART_CAMERA###` on camera failure              | `OCTO-2125` |
| `TC_bagheera_149`   | `tests/bagheera/test_tc_bagheera_149_stop_rec.py`                                         | `RECORD_STOP for cam_num N` in ndcentral log          | `OCTO-2274` |
| `TC_bagheera_161`   | `tests/bagheera/test_tc_bagheera_161_verify_inward_video_storage.py`                      | Inward camera file stored when config permits         | `OCTO-2274`, `OCTO-1974` |
| `TC_bagheera_162`   | `tests/bagheera/test_tc_bagheera_162_verify_fps_width_height_rt.py`                       | FPS / width / height match config for RT stream       | `BG4-985` |
| `TC_bagheera_166`   | `tests/bagheera/test_tc_bagheera_166_verify_pipeline_play_state.py`                       | `CAM{N}.*PLAYING pipeline` in bagheera/cam_rec log    | `OCTO-2274`, `OCTO-2145` |
| `TC_bagheera_167`   | `tests/bagheera/test_tc_bagheera_167_verify_first_camera_frames.py`                       | First frames logged after pipeline PLAYING            | `OCTO-2274`, `BG4-628` |

---

### Flow 4: Low-Definition (LD) Recording

**What happens:** When `outward_ld_enabled`, `inward_ld_enabled`, or `dms_ld_enabled` are active, ndcentral generates companion `.mp4.ld.mp4` files alongside HQ files. The LD files are moved to SD card in the same `copy_or_move_files` thread. On `cam_rec` restart, LD video copy is re-triggered. The log tracks total files recording: `get_num_digital_cam_files_recording: num_cams_enabled=N, outward_ld=N, inward_ld=N, dms_ld=N, total files recording=N`.
**When active:** When any `*_ld_enabled = true` in config (default: enabled if not overridden)
**Frequency:** Continuous alongside HQ recording
**Cross-service impact:** Increases total file count tracked by ndcentral; DMS LD adds one extra file per session

**Test cases that validate this flow:**
| Test Case ID        | Python Path                                                                                | What it checks                                        | Related Bugs |
| ------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------- | --- |
| `TC_bagheera_2877`  | `tests/bagheera/test_tc_bagheera_2877_ld_pipeline_resolution_bitrate_config.py`           | LD pipeline resolution and bitrate match config       | — |
| `TC_bagheera_2992`  | `tests/bagheera/test_tc_bagheera_2992_ld_video_copy_cam_rec_restart.py`                   | LD video re-copied after cam_rec restart              | — |
| `TC_bagheera_3000`  | `tests/bagheera/test_tc_bagheera_3000_dms_ld_copy_log.py`                                 | DMS LD file copy logged on session completion         | — |
| `TC_bagheera_3282`  | `tests/bagheera/test_tc_bagheera_3282_ld_hd_file_start_time_diff.py`                      | LD and HD file start timestamps within tolerance      | — |
| `TC_bagheera_3284`  | `tests/bagheera/test_tc_bagheera_3284_hd_ld_file_ending.py`                               | LD and HD files end together on session close         | — |
| `TC_bagheera_3326`  | `tests/bagheera/test_tc_bagheera_3326_hd_ld_file_size_regular_privacy.py`                 | LD+HD file sizes correct under regular privacy        | — |
| `TC_bagheera_3327`  | `tests/bagheera/test_tc_bagheera_3327_ld_hd_file_size_enhanced_privacy.py`                | LD+HD file sizes correct under enhanced privacy       | — |
| `TC_bagheera_3386`  | `tests/bagheera/test_tc_bagheera_3386_ld_hd_file_size_service_stop.py`                    | LD+HD sizes correct when service stopped mid-session  | — |
| `TC_bagheera_3388`  | `tests/bagheera/test_tc_bagheera_3388_verify_after_inward_camera_crash.py`                | LD/HD files intact after inward cam crash recovery    | `OCTO-2274`, `OCTO-2191` |
| `TC_bagheera_3390`  | `tests/bagheera/test_tc_bagheera_3390_hd_ld_file_size_phone_reboot.py`                    | LD+HD sizes correct after device reboot               | — |
| `TC_bagheera_3391`  | `tests/bagheera/test_tc_bagheera_3391_hd_ld_file_size_crank_off.py`                       | LD+HD sizes correct after crank-off event             | — |
| `TC_bagheera_3392`  | `tests/bagheera/test_tc_bagheera_3392_hd_ld_file_size_lpw.py`                             | LD+HD sizes correct after low-power wakeup            | — |
| `TC_bagheera_3393`  | `tests/bagheera/test_tc_bagheera_3393_hd_ld_file_size_cyclic_reboot.py`                   | LD+HD sizes correct after cyclic reboot               | — |
| `TC_bagheera_3394`  | `tests/bagheera/test_tc_bagheera_3394_hd_ld_size_outward_camera_crash.py`                 | LD+HD sizes correct after outward cam crash           | `OCTO-2113`, `OCTO-2145` |
| `TC_bagheera_3395`  | `tests/bagheera/test_tc_bagheera_3395_hd_stop_msg_ld_disabled.py`                         | HD stop message sent even when LD is disabled         | — |

---

### Flow 5: RT Frames & Streaming

**What happens:** ndcentral manages realtime frame streaming over ZMQ sockets for outward cam (`ipc:///dev/shm/MSGQ/6355`) and inward cam (`ipc:///dev/shm/MSGQ/6356`). The `crank_level_RT_thread` flag controls whether RT frames are sent (1 = ignition on). RT frame parameters (`rt_outward_fps`, `rt_outward_width`, `rt_outward_height`) are logged at startup. Streaming bitrate consistency is tracked separately.
**When active:** When streaming is enabled in config and ignition is on (`crank_level_RT_thread = 1`)
**Frequency:** Continuous during active driving session
**Cross-service impact:** Analytics service receives RT frames for inference (`ANALYTICS_SERVICE_RESTARTED`)

**Test cases that validate this flow:**
| Test Case ID        | Python Path                                                                                | What it checks                                        | Related Bugs |
| ------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------- | --- |
| `TC_bagheera_144`   | `tests/bagheera/test_tc_bagheera_144_rt_frames.py`                                        | RT frame data logged in ndcentral at expected rate    | — |
| `TC_bagheera_145`   | `tests/bagheera/test_tc_bagheera_145_rt_frames_received_analytics.py`                     | Analytics service receives RT frames from ndcentral   | — |
| `TC_bagheera_2948`  | `tests/bagheera/test_tc_bagheera_2948_consistent_bitrate_live_streaming.py`               | Livestream bitrate remains consistent                 | `DT-3517`, `BG4-785`, `DT-3471` |

---

### Flow 6: xattr Metadata & Database

**What happens:** When `[sdcard] use_extended_attr = true` (or privacy is disabled), ndcentral stamps each completed video/audio file with extended attributes (xattrs) encoding: `time`, `dur`, `size`, `type`, `status`, `cam`, `tc`, `fc_type`, `udid`, `sid`, `upl`, `rec`, `drp`. The `ND_CB_UTILS` tag handles xattr write and read-back. Log: `Record privacy is disabled: 1, so we are storing metadata as xattrs in the original file: <filename>`. Failure path logs `Extended attributes not enabled`.
**When active:** When `use_extended_attr = true` OR when record privacy is not blocking the file
**Frequency:** Once per completed file, in the `copy_or_move_files` thread
**Cross-service impact:** circ_buff reads xattrs when queuing files for upload

**Test cases that validate this flow:**
| Test Case ID        | Python Path                                                                                | What it checks                                        | Related Bugs |
| ------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------- | --- |
| `TC_bagheera_3402`  | `tests/bagheera/test_tc_bagheera_3402_xattr_metadata_db_regular_privacy.py`               | xattrs set correctly under regular privacy            | `DT-3749`, `DT-3495` |
| `TC_bagheera_3414`  | `tests/bagheera/test_tc_bagheera_3414_metadata_database_storage.py`                       | Metadata persisted in DB after session                | `DT-3749` |
| `TC_bagheera_3493`  | `tests/bagheera/test_tc_bagheera_3493_graceful_failure_log_validation.py`                 | Graceful failure when xattr write fails               | — |
| `TC_bagheera_3494`  | `tests/bagheera/test_tc_bagheera_3494_xattr_metadata_db_alert_session.py`                 | xattrs correct for user-alert-triggered session       | `DT-3749` |
| `TC_bagheera_3514`  | `tests/bagheera/test_tc_bagheera_3514_xattr_metadata_db_nonprivacy.py`                    | xattrs set when privacy is fully disabled             | — |
| `TC_bagheera_3522`  | `tests/bagheera/test_tc_bagheera_3522_xattr_metadata_db_enhanced_privacy.py`              | xattrs set correctly under enhanced privacy           | `DT-3442` |
| `TC_bagheera_3523`  | `tests/bagheera/test_tc_bagheera_3523_xattr_partial_file_metadata_db.py`                  | xattrs present on partial files moved at startup      | `DT-3495` |

---

### Flow 7: Partial File Recovery (Boot)

**What happens:** At startup, the `copy_or_move_files` thread finds any `*_partial.csv` or `*_y.mp4` files left in `/home/iriscli/files/` from the previous session (truncated by crash or power loss). It reads privacy state from the partial CSV, copies allowed partial files to the SD card / circular buffer folder, generates a checksum file (`.chm.*`), and logs `start: Move partial file <name> to sdcard` ... `end: Move partial file`. DMS partial files are handled separately.
**When active:** Always at boot if partial files exist
**Frequency:** Once per boot; processes all leftover files
**Cross-service impact:** Circ_buff ingests the recovered partial files for upload

**Test cases that validate this flow:**
| Test Case ID        | Python Path                                                                                | What it checks                                        | Related Bugs |
| ------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------- | --- |
| `TC_bagheera_2573`  | `tests/bagheera/test_tc_bagheera_2573_reboot_within_10min_crash.py`                       | Partial files recovered after crash-triggered reboot  | `OCTO-2274` |
| `TC_bagheera_2796`  | `tests/bagheera/test_tc_bagheera_2796_file_copy_sdcard_partial_session.py`                | Partial session file copied to SD card at boot        | — |
| `TC_bagheera_3321`  | `tests/bagheera/test_tc_bagheera_3321_partial_files_after_reboot.py`                      | All partial files present after clean reboot          | — |
| `TC_bagheera_2626`  | `tests/bagheera/test_tc_bagheera_2626_video_duration_partial_privacy.py`                  | Video duration correct for privacy-partial file       | `DT-3442` |
| `TC_bagheera_2745`  | `tests/bagheera/test_tc_bagheera_2745_privacy_state_partial_files.py`                     | Privacy state read correctly from partial CSV         | — |
| `TC_bagheera_2808`  | `tests/bagheera/test_tc_bagheera_2808_outward_video_upload_idms_offduty_partial.py`       | Outward video partial upload in off-duty mode         | `DT-3377` |
| `TC_bagheera_2842`  | `tests/bagheera/test_tc_bagheera_2842_all_video_upload_idms_offduty_partial.py`           | All video partial uploads in off-duty mode            | — |

---

### Flow 8: Camera Crash Detection & Recovery

**What happens:** ndcentral tracks per-camera crash counts (`cam_crash_count[]`) capped at a max of 5. Each `cam_num N is crashed` message increments the counter. When the count reaches max, the service stops attempting restart. Crash events are logged to CAMERA_CRASH_DB and SIDE_CAM_CRASH_INFO. DB corruption is self-healing — on detected corruption the service re-creates the table. Linux signal crashes (SIGSEGV etc.) are logged as `Linux signal crash exit`.
**When active:** Always; triggered by camera pipeline errors
**Frequency:** On crash events; DB write per crash
**Cross-service impact:** bagheera service receives `###RESTART_CAMERA###`; analytics restarts are tracked via `ANALYTICS_SERVICE_RESTARTED`

**Test cases that validate this flow:**
| Test Case ID        | Python Path                                                                                | What it checks                                        | Related Bugs |
| ------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------- | --- |
| `TC_bagheera_2608`  | `tests/bagheera/test_tc_bagheera_2608_validate_camera_crash_db.py`                        | Camera crash DB updated after crash event             | `OCTO-2274`, `OCTO-2191`, `OCTO-2129` |
| `TC_bagheera_2795`  | `tests/bagheera/test_tc_bagheera_2795_camera_crash_db_corruption_restoration.py`          | Corrupted crash DB is re-created on next boot         | `OCTO-2129`, `OCTO-2274` |
| `TC_bagheera_2859`  | `tests/bagheera/test_tc_bagheera_2859_linux_signal_crash_exit.py`                         | Service logs Linux signal crash reason on exit        | `BG4-695`, `DT-3391`, `DT-3841`, `MOW-714` |
| `TC_bagheera_2873`  | `tests/bagheera/test_tc_bagheera_2873_side_cameras_disabled_crash_frequency.py`           | Side cameras do not crash when disabled in config     | `DT-3577`, `OCTO-1983` |
| `TC_bagheera_3493`  | `tests/bagheera/test_tc_bagheera_3493_graceful_failure_log_validation.py`                 | Graceful failure with useful log on non-crash exits   | — |

---

### Flow 9: DMS (Driver Monitoring System) Integration

**What happens:** ndcentral monitors `dms_connection_status.bin` in `/dev/shm/nd_files_c/` to track whether the DMS camera is connected. It handles `CAMREC_DMS_CONNECTION_STATUS_MSG` from cam_rec. The `NDC_RT` tag logs `dms_cam_rt_streaming_enabled` and `drowsy_enabled` flags. When DMS drowsy detection is active, ndcentral coordinates drowsy alerts. DMS LD files are tracked separately in `get_num_digital_cam_files_recording` (`dms_ld=N`).
**When active:** When DMS camera is connected and `drowsy.audio = true` is set; DMS LD when `dms_ld_enabled = true`
**Frequency:** Status checked at startup; DMS connection messages are event-driven
**Cross-service impact:** DMS uses a separate camera pipeline; health stats service receives DMS health data

**Test cases that validate this flow:**
| Test Case ID        | Python Path                                                                                | What it checks                                        | Related Bugs |
| ------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------- | --- |
| `TC_bagheera_1806`  | `tests/bagheera/test_tc_bagheera_1806_dms_camera_start_recording.py`                      | DMS camera starts recording after `CAMREC_DMS_...`    | `DT-3958` |
| `TC_bagheera_1807`  | `tests/bagheera/test_tc_bagheera_1807_dms_frame_process.py`                               | DMS frames processed and logged                       | `DT-3958` |
| `TC_bagheera_1813`  | `tests/bagheera/test_tc_bagheera_1813_dms_master.py`                                      | Full DMS pipeline: camera, frames, alerts             | `DT-3958`, `OCTO-2163` |
| `TC_bagheera_2169`  | `tests/bagheera/test_tc_bagheera_2169_dms_drowsy_enabled.py`                              | `drowsy_enabled: 1` logged at startup                 | `OCTO-2163` |
| `TC_bagheera_2173`  | `tests/bagheera/test_tc_bagheera_2173_dms_drowsy_disabled_camera_disabled.py`             | DMS camera inactive when drowsy+cam both disabled     | — |
| `TC_bagheera_2174`  | `tests/bagheera/test_tc_bagheera_2174_dms_disable_drowsy_enable_camera.py`                | DMS camera runs even when drowsy is disabled          | — |
| `TC_bagheera_2175`  | `tests/bagheera/test_tc_bagheera_2175_dms_behavior_privacy_disabled.py`                   | DMS behavior unchanged when privacy is disabled       | — |
| `TC_bagheera_2176`  | `tests/bagheera/test_tc_bagheera_2176_dms_behavior_privacy_enabled.py`                    | DMS behavior under privacy-enabled mode               | — |
| `TC_bagheera_2916`  | `tests/bagheera/test_tc_bagheera_2916_dms_health_data_hs.py`                              | DMS health data forwarded to HealthStats service      | — |
| `TC_bagheera_3000`  | `tests/bagheera/test_tc_bagheera_3000_dms_ld_copy_log.py`                                 | DMS LD file copied and logged correctly               | — |

---

### Flow 10: Privacy Mode (Core)

**What happens:** ndcentral loads the full privacy configuration at startup and logs `Privacy configuration loaded - save_user_alert_video=N, enhanced_privacy=N, off_duty_mode=N, upload_video[...]`. Privacy state dictates per-cam `rec_vid_enabled` / `upl_vid_enabled` flags. The privacy LED is driven by ndcentral (`LED will indicate privacy mode`). Speed-based and ignition-based triggers are evaluated in real time. Blackout mode (`session case in blackout`) prevents recording/upload entirely. For mode-specific detail see the dedicated privacy skills.
**When active:** Always — privacy is always in some state
**Frequency:** State machine driven by events (ignition, speed, button, timer)
**Cross-service impact:** Privacy state gates `cam_rec` recording, `circ_buff` upload queuing, and `audio` PCM file creation

**Test cases that validate this flow (core / non-mode-specific):**
| Test Case ID        | Python Path                                                                                | What it checks                                        | Related Bugs |
| ------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------- | --- |
| `TC_bagheera_159`   | `tests/bagheera/test_tc_bagheera_159_check_privacy_led.py`                                | LED state reflects privacy on/off                     | — |
| `TC_bagheera_165`   | `tests/bagheera/test_tc_bagheera_165_verify_privacy_set.py`                               | Privacy config flags match override INI values        | `DT-3442`, `BG4-514` |
| `TC_bagheera_2853`  | `tests/bagheera/test_tc_bagheera_2853_blackout_privacy_speed_enabled.py`                  | Blackout (speed-based privacy) blocks recording       | `DT-3442`, `DT-3758` |
| `TC_bagheera_2941`  | `tests/bagheera/test_tc_bagheera_2941_no_unintended_privacy_speed.py`                     | Privacy not triggered unintentionally by speed noise  | `DT-3442`, `DT-3758` |
| `TC_bagheera_2860`  | `tests/bagheera/test_tc_bagheera_2860_audio_toggle_enhanced_privacy.py`                   | Audio toggles correctly when enhanced privacy changes | `BG4-837` |
| `TC_bagheera_152`   | `tests/bagheera/test_tc_bagheera_152_verify_irled.py`                                     | IR LED state correct for inward cam active/inactive   | `BG4-608`, `BG4-561` |
| `TC_bagheera_2815`  | `tests/bagheera/test_tc_bagheera_2815_irled_value_default_logic.py`                       | IR LED value uses default logic when not overridden   | `BG4-608`, `BG4-561` |
| `TC_bagheera_1785`  | `tests/bagheera/test_tc_bagheera_1785_no_audio_alert_disabled_inward.py`                  | No audio alert when inward cam is disabled            | `BG4-837` |

> **Privacy mode-specific TCs** are covered by dedicated skills:
> - Regular Privacy → `/BAGHEERA_Regular_Privacy` (TC-1413 to TC-1464)
> - Disabled Privacy → `/BAGHEERA_Disabled_Privacy` (TC-1465 to TC-1481, TC-1919)
> - Enhanced Privacy → `/BAGHEERA_Enhanced_Privacy` (TC-1519 to TC-1621, TC-1920)
> - Off-Duty Privacy → `/BAGHEERA_Offduty_Privacy` (TC-1570 to TC-1595)
> - Custom Privacy → `/BAGHEERA_Custom_Privacy`

---

### Flow 11: Audio Recording

**What happens:** ndcentral enables audio when `[camera] audio_enable = true`. Audio files are PCM (`.pcm`) then compressed to AAC (`.aac`) and zipped (`.zip`). Empty PCM files (`0 size`) are deleted automatically. The `ADD_FILE_DB` log entries appear for `.aac` and `.zip` audio files alongside video entries. Unsupported audio codec crashes during playback are logged separately.
**When active:** When `audio_enable = true` in config
**Frequency:** One audio file per recording session
**Cross-service impact:** `audio` service writes PCM; ndcentral queues compressed audio to circ_buff

**Test cases that validate this flow:**
| Test Case ID        | Python Path                                                                                | What it checks                                        | Related Bugs |
| ------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------- | --- |
| `TC_bagheera_2137`  | `tests/bagheera/test_tc_bagheera_2137_audio_file_generation_dt_1814.py`                   | Audio file (AAC/ZIP) generated per session on D1814   | `OCTO-2168`, `OCTO-2180`, `BG4-810` |
| `TC_bagheera_2789`  | `tests/bagheera/test_tc_bagheera_2789_audio_playback_crash_unsupported.py`                | Graceful handling of unsupported audio playback       | `OCTO-2180`, `BG4-810` |

---

### Flow 12: Ignition & Power Events

**What happens:** ndcentral receives `IGNITION ON` / `IGNITION OFF` from the device PMIC layer and adjusts the `crank_level_RT_thread` flag accordingly. On `IGNITION ON`, a new session is started and RT streaming resumes. On `IGNITION OFF`, the RT frame send-till-time is set to a finite value (`ctx.send_rt_frames_till_time = 40917`). The wake/reset reason string is logged at boot: `WAKEUP_REASON::POWER_UP:IGNITION -- RESET_REASON::SHUTDOWN_STATE:...`.
**When active:** Always — ignition events trigger state transitions
**Frequency:** Event-driven; at least once per drive cycle
**Cross-service impact:** Affects `cam_rec` session start/stop; triggers circ_buff upload enable/disable

**Test cases that validate this flow:**
| Test Case ID        | Python Path                                                                                | What it checks                                        | Related Bugs |
| ------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------- | --- |
| `TC_bagheera_2856`  | `tests/bagheera/test_tc_bagheera_2856_user_alert_capture_delay.py`                        | User-alert capture starts within expected delay       | — |
| `TC_bagheera_2755`  | `tests/bagheera/test_tc_bagheera_2755_changed_command_file_conversion.py`                 | File conversion triggered correctly on command change | — |
| `TC_bagheera_2784`  | `tests/bagheera/test_tc_bagheera_2784_junk_char_verification.py`                          | Junk characters in log do not cause parse failures    | — |
| `TC_bagheera_2785`  | `tests/bagheera/test_tc_bagheera_2785_reoperate_logs_blackout.py`                         | Logs re-open correctly after blackout window ends     | — |

---

### Flow 13: Device-Type-Specific Behavior

**What happens:** ndcentral supports multiple hardware device types (`bagheera2`, `bagheera3`, `krait`, `krait2`, `bagheera4`, `octo`). Certain features activate only on specific device types (e.g., 4-camera side-cams on bagheera2/3, ADB vs serial connection, different SD card paths). Device type is read from config at startup: `Device Type from config file: bagheera3(2)`.
**When active:** Always; device type gates feature availability
**Frequency:** Read once at startup
**Cross-service impact:** Affects all file path, camera count, and connection method decisions

**Test cases that validate this flow:**
| Test Case ID        | Python Path                                                                                | What it checks                                        | Related Bugs |
| ------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------- | --- |
| `TC_bagheera_1860`  | `tests/bagheera/test_tc_bagheera_1860_dt_1300.py`                                         | Behavior correct on device type 1300                  | — |
| `TC_bagheera_1861`  | `tests/bagheera/test_tc_bagheera_1861_dt_1320.py`                                         | Behavior correct on device type 1320                  | — |
| `TC_bagheera_1967`  | `tests/bagheera/test_tc_bagheera_1967_dt_675.py`                                          | Behavior correct on device type 675                   | — |
| `TC_bagheera_2178`  | `tests/bagheera/test_tc_bagheera_2178_dt_1883.py`                                         | Behavior correct on device type 1883                  | — |
| `TC_bagheera_2200`  | `tests/bagheera/test_tc_bagheera_2200_dt_320.py`                                          | Behavior correct on device type 320                   | — |

---

## Config-Driven Flow Activation

The agent MUST read the device config from `device_data/device_<ID>_config.ini` before selecting test cases. Use the following mapping:

| Config Section            | Config Key              | Value          | Activates Flow(s)                              | Test Cases Affected                                     |
| ------------------------- | ----------------------- | -------------- | ---------------------------------------------- | ------------------------------------------------------- |
| `[camera]`                | `audio_enable`          | `true`         | Flow 11: Audio Recording                       | `TC_bagheera_2137`, `TC_bagheera_2789`                  |
| `[camera]`                | `back`                  | `enable`       | Flow 3: Inward camera recording                | `TC_bagheera_034`, `TC_bagheera_161`                    |
| `[camera]`                | `video_encryption`      | `true`         | Flow 3: Encrypted video                        | `TC_bagheera_141`                                       |
| `[sdcard]`                | `use_extended_attr`     | `true`         | Flow 6: xattr Metadata                         | All `TC_bagheera_3402` through `TC_bagheera_3523`       |
| `[drowsy]`                | `audio`                 | `true`         | Flow 9: DMS drowsy detection                   | `TC_bagheera_2169`, `TC_bagheera_2173`, `TC_bagheera_2174` |
| `[outwardcam_streaming]`  | `enabled`               | `true`/`1`     | Flow 5: Outward RT streaming                   | `TC_bagheera_144`, `TC_bagheera_145`, `TC_bagheera_2948` |
| `[inwardcam_streaming]`   | `enabled`               | `true`/`1`     | Flow 5: Inward RT streaming                    | `TC_bagheera_162`                                       |
| `[low_fps]`               | `outward_ld_enabled`    | `true`         | Flow 4: Outward LD recording                   | `TC_bagheera_3282`, `TC_bagheera_3284`, `TC_bagheera_3326` |
| `[low_fps]`               | `inward_ld_enabled`     | `true`         | Flow 4: Inward LD recording                    | `TC_bagheera_3327`, `TC_bagheera_3388`, `TC_bagheera_3392` |
| `[low_fps]`               | `dms_ld_enabled`        | `true`         | Flow 4: DMS LD recording                       | `TC_bagheera_3000`                                      |
| `[privacy_mode]`          | `default_privacy_v3`    | `true`         | Flow 10 → Regular Privacy skill                | See `/BAGHEERA_Regular_Privacy`                         |
| `[privacy_mode]`          | `enhanced_privacy`      | `true`         | Flow 10 → Enhanced Privacy skill               | See `/BAGHEERA_Enhanced_Privacy`                        |
| `[privacy_mode]`          | `off_duty_mode`         | `true`         | Flow 10 → Off-Duty Privacy skill               | See `/BAGHEERA_Offduty_Privacy`                         |
| `[privacy_mode_activate]` | `speed_based`           | `true`         | Flow 10: Speed-based privacy trigger           | `TC_bagheera_2853`, `TC_bagheera_2941`                  |
| —                         | —                       | —              | Flows 1–3, 7–8, 12 (always active)             | All initialization, session, recovery, crash TCs        |

**Rules:**
- Flows marked "always active" → run their test cases unconditionally
- Flows gated by a config key → run only if the device config has that key set to the activating value
- If the config key is missing from device config → use default (LD enabled by default; xattr disabled by default; audio enabled by default)
- Config values in `device_list_config.csv` take precedence if present

---

## Cross-Service Dependencies

| Related Service    | Why                                                                    | When to check its logs                              |
| ------------------ | ---------------------------------------------------------------------- | --------------------------------------------------- |
| `cam_rec`          | Receives `START_NEXT_SESSION` filenames; writes video files            | Flows 2, 3, 4 — recording and LD file generation    |
| `bagheera`         | Pipeline orchestrator; receives `###RESTART_CAMERA###`                 | Flow 3, 8 — camera pipeline and crash recovery      |
| `circ_buff`        | Ingests files added via `ADD_FILE_DB`; handles VOD and upload          | Flows 2, 7 — file routing and partial recovery      |
| `audio`            | Writes PCM files; ndcentral queues compressed audio                    | Flow 11 — audio recording                           |
| `analytics`        | Receives RT frames; sends `ANALYTICS_SERVICE_RESTARTED` on restart     | Flow 5 — RT streaming and inference alerts          |
| `HealthStatsManager` | Receives DMS health data from ndcentral                              | Flow 9 — DMS health data                            |
| `ndcentral` (self) | Log tag `NDC:`, `NDC_RT:`, `GMETA:`, `ND_CB_UTILS:`, `DEVB3:`, `DM:` | All flows — primary log source                      |

---

## Flow Dependency Graph

```
boot → [Flow 1: Init & Config Load]
         → message queues created
         → [Flow 2: Session Management] → send START_NEXT_SESSION every ~60s
         → [Flow 3: Camera Recording]   → START_CAMERA → RECORD_START per cam
             → [Flow 4: LD Recording]   → companion .ld.mp4 per session (if enabled)
             → [Flow 5: RT Streaming]   → ZMQ publish while crank_level=1
         → [Flow 6: xattr Metadata]     → stamp each completed file (if enabled)
         → [Flow 9: DMS Integration]    → parallel DMS camera + drowsy (if connected)
         → [Flow 10: Privacy]           → gates rec_vid/upl_vid per camera
         → [Flow 11: Audio]             → parallel audio PCM/AAC (if enabled)

ignition ON  → crank_level_RT_thread=1 → Flow 2 starts new session, Flow 5 streams
ignition OFF → crank_level_RT_thread=0 → Flow 5 limited, privacy timer may start

crash → [Flow 8: Crash Detection] → cam_crash_count++ → restart or stop

boot (with leftover files) → [Flow 7: Partial File Recovery] → copy_or_move_files runs once

config: enhanced_privacy / off_duty_mode / default_privacy_v3 → [Flow 10 variants]
```

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Determine device type** (`bagheera2`, `bagheera3`, `krait`, `krait2`, `bagheera4`, `octo`) — gates camera count and SD card paths
3. **Determine active flows** using the Config-Driven Flow Activation table above
4. **For privacy-mode-specific flows**, delegate to the relevant privacy skill (`/BAGHEERA_Regular_Privacy`, `/BAGHEERA_Enhanced_Privacy`, `/BAGHEERA_Offduty_Privacy`, `/BAGHEERA_Disabled_Privacy`)
5. **For each active flow**, run the mapped Python test cases from `tests/bagheera/`
6. **Use timestamp filtering** — every ndcentral log line starts with `{epoch_ms}:{uptime_ms}:TAG:`; filter with `awk -F: -v ts=$test_start_ts '$1 >= ts'`
7. **Search device logs** in `device_logs/<device_id>/` — primary log is `ndcentral.log`; secondary logs are `bagheera.log`, `cam_rec.log`, `audio.log`
8. **For cross-service checks**, also search logs of related services listed above when validating Flows 2, 3, 5, 9, 11
9. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED (if the flow's config gate was not active)

### Key Log Path (all device types)
```
/home/ubuntu/.nddevice/log/ndcentral/log_*.log
```

### Timestamp-Filtered Search Template
```bash
test_start_ts=$(date +%s%3N)
grep -iE "<PATTERN>" /home/ubuntu/.nddevice/log/ndcentral/log_*.log 2>/dev/null \
  | awk -F: -v ts=$test_start_ts '$1 >= ts' | tail -5
```

### Critical Init Log Markers
| Pattern | Confirms |
|---------|---------|
| `#### Starting: ND Central ####` | Service started (Flow 1) |
| `success in create_table_db` | DB initialized (Flow 1) |
| `Privacy configuration loaded` | Privacy config parsed (Flow 10) |
| `Data Product low_fps implementation is enabled` | LD recording active (Flow 4) |
| `audio recording is enabled` | Audio enabled (Flow 11) |
| `Enabling IMU sensor` | IMU started (Flow 1) |
| `CAMREC_DMS_CONNECTION_STATUS_MSG` | DMS connected (Flow 9) |
| `send START_NEXT_SESSION message to cam_rec` | Session negotiation (Flow 2) |
| `start: Move partial file` | Partial file recovery started (Flow 7) |
| `Record privacy is disabled.*storing metadata as xattrs` | xattr stamping active (Flow 6) |
