---
name: dms-service-validation
description: "Use when: validating DMS (Driver Monitoring System) service behavior from device logs. Covers DMS camera initialization and recording (cam_num=8 HD+LD), frame processing and NRT/RT analytics, IRLED health monitoring, session metadata (dmsStartTime/dmsStartTimeLd/dmsPtsStartTime/dmsPtsStartTimeLd), LD file size and property validation, privacy mode interactions (enhanced/full/partial), LPM recording, LQ file behavior (store_lq_dms_file), alert session LD file cap (≤80), zero-MB file deletion, video list update, health stats and observation file DMS keys, drowsy enablement via cloud, haptic_feedback service lifecycle, haptic HealthStats envelope, ignition-on haptic audio health check, haptic periodic health check upon bootup, NDC repeated signal hardening, IRLED spurious ret:-1 resilience, and service status (bagheera, circular_buffer, uploader, dmsAnalyticsClient, analyticsService, inference, haptic_feedback)."
argument-hint: "device ID (e.g., /dms-service-validation 440073)"
---

# DMS — Driver Monitoring System Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the DMS subsystem —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads pytest test cases for actual log patterns,
> device-type paths, and acceptance criteria — this skill does NOT duplicate those.

---

## Service Overview

`dmsAnalyticsClient` is the Python analytics service that processes DMS camera (cam_num=8) video frames in real-time and non-real-time to detect drowsiness, gaze deviation, blink rate, head nodding, and PERCLOS. It consumes sessions dispatched by `ndcentral` (`bagheera`) and writes NRT observation data to `/home/iriscli/ND_OUTPUT/<session>/dms_vis_obs.obsdata`. The real-time path (`dmsRealTime`) processes frames as they arrive; the NRT path (`dmsNonRealTime`) runs on full completed sessions.

`haptic_feedback` (`haptic_feedback.service` / `HPTC`) is a companion service that delivers vibration alerts (haptic motor via GPIO/USB hub) when DMS events are raised. It listens on TCP `127.0.0.1:6393`, receives `SessionInfo` messages from `ndcentral`, and writes per-session `haptic_events.json` files to `/home/ubuntu/autocam/<session>/`.

`ndcentral` (`bagheera`) orchestrates all camera sessions and owns the DMS camera session lifecycle: it sends `START_OTHER_CAM_SESSION` and `END_OTHER_CAM_SESSION` messages for cam_num=8 (HD and LD variants), embeds DMS timing metadata in the session JSON blob, and forwards IRLED health info to health stats.

**Process names:** `dmsAnalyticsClient`, `haptic_feedback`, `bagheera` (NDC)
**Log files:**
- `dmsAnalyticsClient.log` → `/home/ubuntu/.nddevice/log/dmsAnalyticsClient/*`
- `haptic_feedback.log` → `/home/ubuntu/.nddevice/log/haptic_feedback/*` *(service-level log is service_mon; haptic writes its own log at `/home/ubuntu/config/haptic_feedback.log` depending on build)*
- `ndcentral.log` → `/home/ubuntu/.nddevice/log/ndcentral/*`
- `cam_rec.log` → `/home/ubuntu/.nddevice/log/cam_rec/*`
- `analytics.log` → `/home/ubuntu/.nddevice/log/analytics/*`
- `inference.log` → `/home/ubuntu/.nddevice/log/inference/*`
- `service_mon.log` → `/home/ubuntu/.nddevice/log/service_mon/*`

**Primary config sections:** `[dms_drowsy]`, `[dms_camera]`, `[camera]`, `[Transcode]`, `[privacy_mode]`, `[haptic_feedback]`, `[healthstats]`

**Supported device types:** `bagheera3`, `octo` (most DMS tests skip on other device types)

---

## Service Flows

### Flow 1: DMS Camera Detection & Recording Start

**What happens:** On boot (after `[dms_drowsy] enabled=1` or `[dms_camera] enabled=true` is set), `cam_rec` detects the DMS camera is connected and enables it. NDC logs `START_OTHER_CAM_SESSION` for cam_num=8 with both `is_LD=0` (HD) and `is_LD=1` (LD, only when `dms_ld_enabled=true`). Both sessions are expected to start within 100ms of each other. NDC also receives frames from cam 8 and logs `Received Frame from Cam 8`.

**When active:** `[dms_drowsy] enabled=1` OR `[dms_camera] enabled=true` (see Flow 2 for interaction)
**Frequency:** Once per session boundary (every ~60s)
**Cross-service impact:** `cam_rec` enables/disables ADD (anti-distraction detection) based on DMS state; `ndcentral` owns session start timestamps

**Key log patterns (cam_rec):**
- `DMS is enabled and Connected: Disable ADD`

**Key log patterns (ndcentral):**
- `START_OTHER_CAM_SESSION message received with session_fname = 8<session>, cam_num = 8, is_LD = 0`
- `START_OTHER_CAM_SESSION message received with session_fname = 8<session>, cam_num = 8, is_LD = 1`
- `END_OTHER_CAM_SESSION message received with session_fname = 8<session>, cam_num = 8`
- `Received Frame from Cam 8 - count <N>, PTS of buffer <pts>`
- `get_num_digital_cam_files_recording: num_cams_enabled=5, outward_ld=1, inward_ld=1, dms_ld=1, total files recording=8`

**Key log patterns (cam_rec):**
- `Frame count received:`
- `dms_nrt_width`, `dms_nrt_height`, `dms_nrt_fps`, `dms_nrt_bitrate` *(NRT video parameters)*

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_1806`        | `tests/DMS/test_tc_dms_1806_dms_camera_start_recording.py`                                  | HD+LD START_OTHER_CAM_SESSION for cam 8, time diff ≤ 100ms  |
| `TC_1807`        | `tests/DMS/test_tc_dms_1807_dms_frame_process.py`                                           | Cam 8 frame reception, session start, frames sent to HS     |
| `TC_2175`        | `tests/DMS/test_tc_dms_2175_dms_behaviour_privacy_disabled.py`                              | HD+LD session start timing with privacy deactivated         |
| `TC_2752`        | `tests/DMS/test_tc_dms_2752_dms_partial_session_video_upload.py`                            | HD+LD session start timestamps cross-checked                |

---

### Flow 2: DMS Enable/Disable Config Logic

**What happens:** NDC reads `[dms_drowsy] enabled` and `[dms_camera] enabled` from config. If `dms_drowsy=1`, DMS recording is automatically enabled regardless of `dms_camera`. If `dms_drowsy=0` but `dms_camera=true`, DMS recording still runs. If `dms_drowsy=0` AND `dms_camera` is absent/false (no override), DMS camera is disabled and no cam-8 files appear on the SD card. Cloud override (via OTA) can push these values without a local config push.

**When active:** Config parsing on every boot/bagheera restart
**Frequency:** Once at boot
**Cross-service impact:** `otacheck` applies cloud config; `cam_rec` acts on the resulting enabled state

**Key log patterns (ndcentral / cam_rec):**
- `dms_drowsy is enabled in nd_config.ini, enable dms recording also`
- `dms_drowsy is disabled in nd_config.ini but dms_camera is enabled in override, enable dms recording`
- `No value present for key enabled in override dictionary`
- `DMS camera is disabled`

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_2169`        | `tests/DMS/test_tc_dms_2169_dms_system_behaviour_dms_drowsy_enabled.py`                    | DMS files appear on SD card when dms_drowsy+dms_camera enabled |
| `TC_2173`        | `tests/DMS/test_tc_dms_2173_dms_drowsy_disabled_then_dms_camera_disabled.py`               | DMS camera disabled when drowsy=0 and no dms_camera override |
| `TC_2174`        | `tests/DMS/test_tc_dms_2174_dms_disable_drowsy_enable_camera_behaviour.py`                 | DMS records when drowsy=0 but dms_camera=true               |
| `TC_2844`        | `tests/DMS/test_tc_dms_2844_dms_drowsy_enabled_via_cloud.py`                               | dms_drowsy enabled via cloud OTA override, DMS files present |

---

### Flow 3: DMS NRT (Non-Real-Time) Analytics Processing

**What happens:** After each 60s session completes, `dmsAnalyticsClient` picks up the session video from `/home/iriscli/ND_INPUT/<session>` and runs NRT processing. It logs `We are going to operate on the video file`, runs `dmsNonRealTime`, and writes `dms_vis_obs.obsdata` to `/home/iriscli/ND_OUTPUT/<session>/`. The log `Wrote nrt session data at /home/iriscli/ND_OUTPUT/<session>` confirms completion.

**When active:** `[dms_drowsy] enabled=1` and DMS camera connected
**Frequency:** Once per completed session (~every 60s)
**Cross-service impact:** NRT data feeds into observation upload pipeline via uploader service

**Key log patterns (dmsAnalyticsClient.log):**
- `We are going to operate on the video file /home/iriscli/ND_INPUT/0<session>`
- `Wrote nrt session data at /home/iriscli/ND_OUTPUT/<session>/dms_vis_obs.obsdata`
- `src.dms.dmsNonRealTime - INFO - Starting DMS NRT`

**Key log patterns (inference.log):**
- `src.dms.dmsNonRealTime - INFO - Starting DMS NRT`

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_1813`        | `tests/DMS/test_tc_dms_1813_dms_nrt_processing.py`                                         | NRT session data written, Starting DMS NRT, NRT params in cam_rec |
| `TC_2751`        | `tests/DMS/test_tc_dms_2751_dms_vision_running.py`                                         | Vision running log for session in dmsAnalyticsClient         |

---

### Flow 4: DMS RT (Real-Time) Analytics Processing

**What happens:** As frames arrive from cam 8, `dmsAnalyticsClient` processes them in real-time. The service starts up a `RealTimeDMS` instance and logs `Initializing RealTimeDMS`. The analytics wrapper begins with `dms_analytics_wrapper: BEGIN`. Frames are confirmed as `Processed frame`. From `analytics` service, `Cam 8 : sending msg with sess_processed=0` and `DMS send_msg to HS: num_frames:` confirm frames are sent to health stats.

**When active:** `[dms_drowsy] enabled=1` and DMS camera connected
**Frequency:** Per frame (real-time), service init once at startup
**Cross-service impact:** Feeds drowsiness events to alert pipeline and health stats

**Key log patterns (dmsAnalyticsClient.log):**
- `dms_analytics_wrapper: BEGIN`
- `src.dms.dmsRealTime       - Initializing RealTimeDMS`
- `Processed frame`
- `Sending message to health stats service: <session>`
- `Detected change in SESSION: from <prev> to <new>`
- `Gathering dms real-time obsdata/events for session(<N>) <session>`
- `Incremented session_count to <N>`

**Key log patterns (analytics.log):**
- `Cam 8 : sending msg  with sess_processed=0 and session_id=`
- `DMS send_msg to HS: num_frames:`

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_1807`        | `tests/DMS/test_tc_dms_1807_dms_frame_process.py`                                           | RT frames prepared and sent to HS, Processed frame log       |
| `TC_2683`        | `tests/DMS/test_tc_dms_2683_dms_rt_processing.py`                                           | dmsAnalyticsClient BEGIN and Initializing RealTimeDMS        |

---

### Flow 5: DMS Session Metadata (dmsStartTime / dmsStartTimeLd)

**What happens:** At session boundary, `ndcentral` extracts DMS timing info and embeds four metadata keys in the session JSON blob sent to health stats and cloud: `dmsStartTime`, `dmsStartTimeLd`, `dmsPtsStartTime`, `dmsPtsStartTimeLd`. These values are logged explicitly before the JSON blob is assembled. The metadata also appears in the video list entry (`"videoName": "0<session>.mp4"`).

**When active:** DMS camera connected and recording
**Frequency:** Once per session end
**Cross-service impact:** These keys appear in the observation file (`dms_vis_obs.obsdata`) and health stats payload

**Key log patterns (ndcentral.log):**
- `GMETA: I: ... dmsStartTime info:::<epoch>:::`
- `GMETA: I: ... dmsStartTimeLd info:::<epoch>:::`
- `GMETA: I: ... dmsPtsStartTime info:::<pts>:::`
- `GMETA: I: ... dmsPtsStartTimeLd info:::<pts>:::`
- `dmsStartTime <epoch> ctx.out_meta_count <N>`
- `"dmsStartTime": <epoch>` *(in JSON blob)*
- `"dmsStartTimeLd": <epoch>`
- `"dmsPtsStartTime": <pts>`
- `"dmsPtsStartTimeLd": <pts>`

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_2802`        | `tests/DMS/test_tc_dms_2802_dms_session_metadata.py`                                       | All 4 DMS metadata keys in ndcentral log and video entry     |
| `TC_2803`        | `tests/DMS/test_tc_dms_2803_dms_partial_session_metadata.py`                               | DMS metadata in partial session (reboot mid-session)         |

---

### Flow 6: DMS LD File Size Validation

**What happens:** When `[camera] dms_ld_enabled=true`, `ndcentral` records a low-quality DMS LD file (`.mp4.ld.mp4`) alongside the HD file for cam 8. After session end, NDC logs `GetFileSize` for the LD file. Expected size is 7–8 MB for a 60s session. The LD file is moved to SD card with the pattern `Move file /home/iriscli/files/8<session>.mp4.ld.mp4 to folder /media/data/nd_sdcard//8<session>.mp4.ld.mp4`.

**When active:** `[dms_drowsy] enabled=1` AND `[camera] dms_ld_enabled=true`
**Frequency:** Once per session
**Cross-service impact:** LD files feed into Transcode/circular buffer pipeline

**Key log patterns (ndcentral.log):**
- `Move file /home/iriscli/files/8<session>.mp4.ld.mp4 to folder /media/data/nd_sdcard//8<session>.mp4.ld.mp4`
- `GetFileSize` *(adjacent to the 8<session> filename in ndcentral log)*

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_2757`        | `tests/DMS/test_tc_dms_2757_dms_ld_file_size.py`                                           | LD file size between 7MB and 8MB                            |
| `TC_2768`        | `tests/DMS/test_tc_dms_2768_dms_ld_file_property.py`                                       | LD file presence on SD card and ffprobe playability          |
| `TC_2840`        | `tests/DMS/test_tc_dms_2840_dms_ffplay_check_ld_recording.py`                              | LD file playable via ffplay/ffprobe (720x720, 30fps)         |
| `TC_2818`        | `tests/DMS/test_tc_dms_2818_dms_ld_recording_config_disabled.py`                           | LD NOT recorded when dms_ld_enabled=false (HD session stops found, LD stop absent) |

---

### Flow 7: DMS Partial Session Handling

**What happens:** If the device reboots mid-session (e.g., during ignition-off), `ndcentral` calls `move_partial_files` on boot to move any in-progress cam-8 files from `/home/iriscli/files/` to the SD card circular buffer folder. Partial DMS files (prefixed `8<session>*`) appear in SD card, and the metadata keys (`dmsStartTime`, etc.) are emitted for the partial session JSON entry.

**When active:** DMS recording active when reboot occurs
**Frequency:** On boot after incomplete session
**Cross-service impact:** `circular_buffer` receives the moved partial files

**Key log patterns (ndcentral.log):**
- `move_partial_files`
- `moving partial files to circular buffer folder and send msg to add to db`
- `Move file /home/iriscli/files/8<session>.* to folder /media/data/nd_sdcard//8<session>.*`
- `dmsStartTime`, `dmsStartTimeLd`, `dmsPtsStartTime`, `dmsPtsStartTimeLd` *(in partial session JSON)*

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_2752`        | `tests/DMS/test_tc_dms_2752_dms_partial_session_video_upload.py`                           | Partial DMS session file presence and timestamp correlation  |
| `TC_2803`        | `tests/DMS/test_tc_dms_2803_dms_partial_session_metadata.py`                               | move_partial_files and all 4 metadata keys for partial session |

---

### Flow 8: DMS Privacy Mode Interactions

**What happens:** DMS recording is sensitive to privacy mode state. Under **full inward privacy** (`privacy_mode.inward=true`), the inward cam (and DMS cam 8) stops recording. Under **enhanced privacy** (`privacy_mode.enhanced_privacy=true`), DMS recording is suppressed. Under **partial inward privacy** (`privacy_mode.inward=true` but `outward=false`), only inward privacy is active and DMS (cam 8) stops. NDC logs privacy state changes and IRLED control (IRLED is turned off under privacy). When ignition-based privacy deactivates on IGNITION_ON, NDC logs `Privacy Mode is Deactivated` and right LED turns green.

**When active:** Privacy mode activated (speed/ignition/button based)
**Frequency:** On privacy state transitions
**Cross-service impact:** LED color changes (inward_led_color red/green/purple), DMS session stop/start

**Key log patterns (ndcentral.log):**
- `ignition_based_privacy:1`
- `IGNITION ON received`
- `Privacy Mode is Deactivated`
- `check_set_irled: Turning off IRLED because of privacy being true`
- `IRLED state unchanged (0), skipping update for reason 2`
- `inside turn_irled_on_or_off`
- `outward_cam_privacy: 0`, `inward_cam_privacy: 1` *(in session JSON blob)*

**Key log patterns (cam_rec):**
- `Received SET_DMS_LED_MSG message to set 2` *(drowsy LED = value 2)*
- `send_session_end_msg_to_bagheera with session_fname: 8<session>`

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_2175`        | `tests/DMS/test_tc_dms_2175_dms_behaviour_privacy_disabled.py`                             | Ignition-based privacy deactivate → LED green, DMS records  |
| `TC_2176`        | `tests/DMS/test_tc_dms_2176_dms_behaviour_privacy_enabled.py`                              | DMS behavior when privacy is enabled with dms_drowsy config  |
| `TC_2804`        | `tests/DMS/test_tc_dms_2804_dms_ld_recording_partial_privacy.py`                           | LD recording with partial inward privacy active              |
| `TC_2812`        | `tests/DMS/test_tc_dms_2812_dms_enhanced_privacy.py`                                       | DMS LED state and recording behavior under enhanced privacy  |
| `TC_2813`        | `tests/DMS/test_tc_dms_2813_dms_full_privacy.py`                                           | DMS LD recording behavior under full privacy (inward=true)   |

---

### Flow 9: DMS LD Recording Under Low Power Mode (LPM)

**What happens:** When ignition is turned off and the device enters low-power wakeup (LPW) cycle, DMS LD recording sessions must be properly closed, partial files moved, and no new DMS sessions started during LPM period. The crank-off flow (`[power] crank_shutdown_duration`, `lowpower_wakeup_cycle_duration`) triggers session end for cam 8 before shutdown.

**When active:** `[dms_drowsy] enabled=1`, `[camera] dms_ld_enabled=true`, and ignition-off
**Frequency:** On every crank-off event
**Cross-service impact:** `power_monitor` orchestrates crank-off; NDC ends DMS sessions before power-down

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_2814`        | `tests/DMS/test_tc_dms_2814_dms_ld_recording_lpm.py`                                       | DMS LD session cleanup and no-recording during LPM cycle     |

---

### Flow 10: DMS LQ File Behavior (store_lq_dms_file)

**What happens:** When `[Transcode] store_lq_dms_file=false`, non-alert DMS LD files are deleted from the SD card after 5 sessions. Alert DMS LD files (tagged `FILE_COMPRESSION=2` in `circular_buffer.db`) are retained. When `store_lq_dms_file=true`, all DMS LD files are retained up to `num_dms_videos_max`. The `circular_buffer.db` `VIDFILES` table tracks these as `NAME LIKE '8%.ld.mp4'`.

**When active:** `[dms_drowsy] enabled=1` and `[Transcode]` section configured
**Frequency:** Per session completion / circular buffer rotation
**Cross-service impact:** `circular_buffer` manages retention; `circ_buff` log shows `added file list` and `deleted file list`

**Key log patterns (ndcentral.log):**
- `Move file /home/iriscli/files/8<session>.mp4.ld.mp4 to folder /media/data/nd_sdcard//8<session>.mp4.ld.mp4`

**Key DB queries (circular_buffer.db):**
- `SELECT COUNT(*) FROM VIDFILES WHERE NAME LIKE '8%.ld.mp4' AND FILE_COMPRESSION = 2 AND TIME > <epoch>` → alert LD files
- `SELECT COUNT(*) FROM VIDFILES WHERE NAME LIKE '8%.ld.mp4' AND FILE_COMPRESSION = 0 AND TIME > <epoch>` → non-alert LD files

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_2823`        | `tests/DMS/test_tc_dms_2823_dms_lq_file_behavior_when_store_lq_dms_is_false.py`            | Non-alert LD deleted after 5 sessions when store_lq_dms=false |
| `TC_2824`        | `tests/DMS/test_tc_dms_2824_dms_lq_alert_file_behavior_when_store_lq_dms_is_false.py`      | Alert LD files retained even when store_lq_dms=false         |
| `TC_2835`        | `tests/DMS/test_tc_dms_2835_dms_lq_file_behavior_when_store_dms_lq_is_false.py`            | Alert vs non-alert LD file count when store_lq_dms=true      |

---

### Flow 11: DMS Alert Session LD File Cap (≤ 80)

**What happens:** When alert events (user alerts or DMS alerts) accumulate, the DMS LD alert file count in `circular_buffer.db` must not exceed 80 entries (enforced by `num_dms_videos_max` default). The circular buffer rotation deletes oldest alert LD files once the cap is reached. This is validated by counting `FILE_COMPRESSION=2` entries in VIDFILES for `8%.ld.mp4` names.

**When active:** `[dms_drowsy] enabled=1`, `[Transcode] store_lq_dms_file=false`
**Frequency:** After sustained alert injection (~4980s at 30s intervals)
**Cross-service impact:** `circular_buffer` enforces cap and deletes oldest entries

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_2836`        | `tests/DMS/test_tc_dms_2836_dms_alert_session_do_not_exceed_80.py`                         | Alert LD file count ≤ 80 after prolonged alert injection     |

---

### Flow 12: DMS Zero-MB File Deletion

**What happens:** On device boot, `ndcentral` scans for zero-size DMS LD files (`.mp4.ld.mp4`) in `/home/iriscli/files/` and deletes them without adding them to `circular_buffer.db`. This prevents corrupt 0-byte files from polluting the database.

**When active:** Always, on every boot with DMS enabled
**Frequency:** Once at boot
**Cross-service impact:** `circular_buffer.db` count for the affected filename must be 0

**Key DB query:**
- `SELECT count(*) FROM VIDFILES WHERE NAME = '8_trip.mp4.ld.mp4'` → expected `0`

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_2822`        | `tests/DMS/test_tc_dms_2822_dms_zero_mb_file_deletion.py`                                  | 0MB LD file deleted from SD card and absent from CB DB       |

---

### Flow 13: DMS Video List Update

**What happens:** When DMS LD alert files are added to the circular buffer, the `circ_buff` service logs `Below is the added file list` and `Below is the deleted file list` sections. DMS LD alert files (`8*.mp4`) in the added list should be present on the SD card at `/media/data/nd_sdcard/`. Timestamp differences between consecutive file entries should be consistent.

**When active:** DMS recording active with `store_lq_dms_file=false` and alert triggers
**Frequency:** On each circular buffer rotation cycle
**Cross-service impact:** `circular_buffer` manages the video list

**Key log patterns (circ_buff.log):**
- `Below is the added file list`
- `file = 8<session>.mp4` *(DMS alert LD entries)*
- `Below is the deleted file list`

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_2849`        | `tests/DMS/test_tc_dms_2849_dms_verify_video_list_update.py`                               | DMS LD alert files in added list present on SD card          |

---

### Flow 14: DMS Health Stats and Observation File Keys

**What happens:** After each session, `dmsAnalyticsClient` sends a health stats message to the `HealthStatsManager`. The HS payload (compressed `.gz` in `/home/ubuntu/.nddevice/log/health/backup/`) contains `dmsAnalyticsClient.rt_info` with DMS sub-keys. The observation file summary JSON contains: `dmsStartTime`, `dmsPtsStartTime`, `dmsStartTimeLd`, `dmsPtsStartTimeLd`, `dms`, `dms_drowsy`, `dms_camera_health`, `dms_calib_data`, `dms_gaze_detection`.

**When active:** `[dms_drowsy] enabled=1`, `[healthstats] videohealthstats_secs` configured
**Frequency:** Per `videohealthstats_secs` interval (default/configured)
**Cross-service impact:** HealthStatsManager publishes payload; uploader uploads observation files

**Key log patterns (dmsAnalyticsClient.log):**
- `Sending message to health stats service: <session>`
- `src.dms.modules.dms_camera_health - DMS Camera health session info: {...}`
- `[SERIALIZATION] dms_camera_obstruction - <ms>`
- `[SERIALIZATION] dms_drowsy - <ms>`
- `[SERIALIZATION] dms_gaze_detection - <ms>`
- `[SERIALIZATION] dms_perclos - <ms>`

**Required HS payload keys:**
- `dmsAnalyticsClient.rt_info` section
- `dms_camera_health` subsection

**Required observation file keys:**
- `dmsStartTime`, `dmsPtsStartTime`, `dmsStartTimeLd`, `dmsPtsStartTimeLd`
- `dms`, `dms_drowsy`, `dms_camera_health`, `dms_calib_data`, `dms_gaze_detection`

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_2850`        | `tests/DMS/test_tc_dms_2850_dms_verify_keys_for_health_and_obs.py`                         | DMS keys in both observation file and HS payload             |

---

### Flow 15: DMS IRLED Health Monitoring

**What happens:** On every new session start, `ndcentral` checks the DMS camera's IR LED status via `dms_irled_event` messages (event 2 = voltage check, event 3 = set color). `Handling DMS_IRLED_EVENT_SET_LED_COLOR` and `Handling DMS_IRLED_EVENT_CHECK_VOLTAGE` are logged. If the IRLED voltage is within limits (register value `0x1a`), NDC logs `DMS irled voltage is within limits. 0x1a` and reports `dms_irled_status:0` in health info. This health info is forwarded to `bagheera` (`Sending DMS health_info to bagheera: dms_irled_status:0`) and to health stats (`Sending IRLED info to healthstats`). The IRLED health register values (`fault_register_values`, `config_register_values`, `irled_status`) appear in the session JSON blob.

**When active:** DMS camera connected and recording
**Frequency:** Once per new session start, then periodically
**Cross-service impact:** Health stats receives IRLED info; session JSON carries `irled_status` and `irled_states`

**Key log patterns (ndcentral.log):**
- `New session is about to start. Check for status of DMS IRLED in current session.`
- `dms_irled_event received: 3, from msgq_dms_irled`
- `dms_irled_event received: 2, from msgq_dms_irled`
- `Handling DMS_IRLED_EVENT_SET_LED_COLOR event`
- `Handling DMS_IRLED_EVENT_CHECK_VOLTAGE event`
- `DMS irled voltage is within limits. 0x1a`
- `Sending DMS health_info to bagheera: dms_irled_status:0`
- `CAMREC_DMS_HEALTH_INFO_MSG message received with irled status:0, SN:<serial>, sensor temperature:-1, fault register values:[0x1a, ...]`
- `Sending IRLED info to healthstats: filename = <session>, irled_status = 0`
- `"irled_status": 0, "irled_states": [{"status": 0, "time": <epoch>}]` *(in session JSON)*
- `Set irled_mode for <gps_coords> as irled_status: 0, time: <epoch>`
- `irled_status: 0, irled_states_len: 1`
- `Setting irled status: 0, irled states: [...]  in metadata`

**Key log patterns (cam_rec.log):**
- `Sending DMS health_info to bagheera: dms_irled_status:0`
- `dmsIRLED.*ret:-1` or `dmsIrledDriverSetup.*ret:-1` *(spurious errors — should NOT cause disconnect)*

**Required HS payload keys:**
- `health_info.gpio_accessories_info.dms_camera_info.irled_status` or similar section

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_3778`        | `tests/DMS/test_tc_dms_3778_dms_check_irled_health_status.py`                              | IRLED log patterns and irled_status in HS payload            |
| `TC_3779`        | `tests/DMS/test_tc_dms_3779_dms_check_fault_register_health_status.py`                     | Fault register values in HS payload                          |
| `TC_3793`        | `tests/DMS/test_tc_dms_3793_dms_check_config_register_health_status.py`                    | Config register values in HS payload                         |
| `TC_2837`        | `tests/DMS/test_tc_dms_2837_dms_led_irled_behaviour.py`                                    | LED and IRLED behavior during DMS recording                  |
| `TC_4160`        | `tests/DMS/test_tc_dms_4160_irled_spurious_ret_minus1_resilience.py` *(formerly TC_3804)*  | ret:-1 from IRLED driver does NOT trigger DMS disconnect     |

---

### Flow 16: DMS Service Status and Crash Resilience (NDC / dmsAnalyticsClient / analyticsService)

**What happens:** The `service_mon` service monitors `bagheera` (NDC label), `dmsAnalyticsClient` (DMSA label), and `analyticsService` (AnalyticsService label). On crash (SIGABRT / kill -6), `service_mon` logs `Service error: NDC :` / `Service error: DMSA :` / `Service error: AnalyticsService :` and the process is automatically respawned by systemd. The service start timestamp difference between `service_mon` log and systemd `ActiveEnterTimestamp` is expected to be within ±15s. Repeated NDC crashes (3× rapid SIGABRT) must NOT trigger an `svc` keep-alive reboot (DT-3963).

**When active:** Always (service monitor always running)
**Frequency:** On crash events
**Cross-service impact:** `svc` (SVC service) monitors keep-alive heartbeats; excessive crashes may trigger reboot if not properly hardened

**Key log patterns (service_mon.log):**
- `Service started: NDC :` *(bagheera)*
- `Service started: dmsAnalyticsClient :`
- `Service started: AnalyticsService :`
- `Service started: Inference :`
- `Service started: Inference_inertial :`
- `Service stopped: Inference_inertial :`
- `Service error: NDC :`
- `Service error: DMSA :` *(or `Service error: dmsAnalyticsClient :`)*
- `Service error: AnalyticsService :`
- `Service error: HPTC :` *(haptic_feedback)*

**Key assertions:**
- After 3 rapid NDC SIGABRTs: no `svc.*reboot` or `keep-alive.*reboot` in service_mon; system uptime > 120s
- Each crash results in per-process respawn (≥3 `Service error: NDC :` entries)

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_520`         | `tests/DMS/test_tc_dms_520_dms_status_check_bagheera.py`                                   | NDC service_mon start timestamp, SIGABRT respawn             |
| `TC_524`         | `tests/DMS/test_tc_dms_524_dms_status_check_circular_buffer.py`                            | CB service_mon start timestamp, SIGABRT respawn              |
| `TC_534`         | `tests/DMS/test_tc_dms_534_dms_status_check_uploader.py`                                   | UPL service_mon start timestamp, SIGABRT respawn             |
| `TC_541`         | `tests/DMS/test_tc_dms_541_dms_status_check_inference.py`                                  | Inference and Inference_inertial start/stop in service_mon   |
| `TC_2830`        | `tests/DMS/test_tc_dms_2830_dms_status_check_dmsanalytics.py`                              | dmsAnalyticsClient service_mon timestamp, SIGABRT respawn    |
| `TC_2831`        | `tests/DMS/test_tc_dms_2831_dms_status_check_analyticsservice.py`                          | analyticsService start/stop timestamp, SIGABRT+SIGTERM cycle |
| `TC_4159`        | `tests/DMS/test_tc_dms_4159_ndc_repeated_signal_hardening.py` *(formerly TC_3803)*         | 3× rapid SIGABRT to NDC → no svc reboot, system uptime > 120s |

---

### Flow 17: haptic_feedback Service Lifecycle and Session Events File

**What happens:** `haptic_feedback` (`HPTC`) initializes by reading config (`[haptic_feedback] enabled=1`), creating a GPIO/USB hub device handle, and loading accessory DB (`/home/ubuntu/.nddevice/accessory.db`). It starts a TCP listener on `127.0.0.1:6393` and logs `Starting Haptic Service on tcp://127.0.0.1:6393`. For each session boundary, it receives a `SessionInfo` message and writes `haptic_events.json` to `/home/ubuntu/autocam/<session>/`. If no haptic events occurred, the file contains `[]`. The file is **session-scoped**: a new empty file is created for each session; the file from the previous session is NOT carried over (DT-4007). Periodic health checks read the haptic motor GPIO input and log errors if GPIO read fails.

**When active:** `[haptic_feedback] enabled=1`
**Frequency:** Per session boundary for events file; every ~60s for health monitoring
**Cross-service impact:** `ndcentral` dispatches `SessionInfo` to haptic service; health stats receives `haptic_motor` section

**Key log patterns (haptic_feedback.log):**
- `Logger successfully initialized for haptic_feedback`
- `Device Type from config file: bagheera3(2)`
- `[GPIO] haptic config loaded: is_haptic_paired=1, out(expander=1, port=1, direction=0, trigger_state=1), in(expander=1, port=2, direction=1, trigger_state=0)`
- `[haptic_motor_health_monitoring] config loaded: is_active_monitoring_enabled=1`
- `Starting Haptic Service on tcp://127.0.0.1:6393`
- `Waiting for haptic_feedback related messages`
- `SessionInfo: session_name=<session>, frame_gen_time(extracted from session_name)=<epoch>`
- `First session detected, no previous session to write metadata.`
- `No haptic events to write for session: <session>`
- `Successfully wrote 0 haptic events to /home/iriscli/ND_OUTPUT/<session>/haptic_events.json`
- `Haptic data successfully serialized for session: <session> in the directory: /home/iriscli/ND_OUTPUT`
- `Periodic health: failed to read input GPIO, error: -6` *(USB hub not connected scenario)*
- `USB hub not detected`

**HS payload haptic_motor keys (TC_3801):**
- `connection_status`, `connection_failure_count`, `no_of_alerts`, `no_of_persistent_faults`, `no_of_alerts_skipped_due_to_latency`, `enabled_in_config`

**Session events file path:** `/home/ubuntu/autocam/<session>/haptic_events.json`

> **Note:** `haptic_events.json` is session-scoped by design (DT-4007), but there is currently no
> active test case validating this specific behavior in `tests/DMS/` — a prior `TC_3805` file
> referenced in earlier docs never existed in the repo history. Treat the session-scoping
> description above as background domain knowledge only until a test case is added.

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_4157`        | `tests/DMS/test_tc_dms_4157_haptic_feedback_service_status.py` *(formerly TC_3800)*        | HPTC SIGABRT → service_mon error, SIGTERM → stopped, restart |
| `TC_3801`        | `tests/DMS/test_tc_dms_3801_haptic_feedback_healthstats_envelope.py`                       | haptic_motor section with 6 sub-keys in HS payload at 70s cadence |

---

### Flow 18: haptic_feedback Ignition-ON Audio Health Check and Bootup GPIO Check

**What happens:** On a fresh ignition-on event (guarded by `is_fresh_ignition_start`, see "Ignition-ON Health Check Log Messages" below), `haptic_feedback` decides whether to run a haptic health check with an audible cue. If a health check has already run within the last 24 hours, it logs one of two skip messages and no further action is taken. Otherwise it drives the haptic motor, plays the configured audio file (`active_monitoring_audio_file`), confirms the motor turns off via GPIO read-back, reports `trigger_status:success` to health stats, and emits `SM_E_HPTC_HEALTH_SUCCESS`. Independently, on every bootup, `periodic_health_monitor` runs its first check and the USB hub must be detected with its GPIO lines set correctly; no GPIO read errors should occur since boot.

**When active:** `[haptic_feedback] enabled=1`, `[haptic_motor_health_monitoring] is_active_monitoring_enabled=true` (+ `audio_feedback_if_active_monitoring_enabled=true` for the audio path)
**Frequency:** Ignition-on audio check: once per fresh ignition-on event (throttled to 24h); bootup GPIO check: once per boot
**Cross-service impact:** `audio` service plays the health-check cue; `HealthStatsManager` receives the haptic health trigger result

**Key log patterns (haptic_feedback.log) — ignition-on audio check:**
- `Ignition-ON detection: is_fresh_ignition_start=<0|1>`
- `Not a fresh ignition start, skipping ignition-ON health check` *(skip path 1)*
- `Ignition-ON: health check skipped (already checked within 24hrs)` *(skip path 2)*
- `Audio play request sent for file: <path to .wav>`
- `Haptic alert trigger request received. Type: ignition_on_health_check`
- `USB_HUB_GPIO_1 set to 1`
- `Motor OFF confirmed: input GPIO returned to HIGH (idle) after 700ms`
- `sending haptic health info to hs:` *(must contain `trigger_status` and `success`)*
- `Haptic health check success, source: ignition_on`
- `Sending critical info: SM_E_HPTC_HEALTH_SUCCESS`

**Key log patterns (audio.log):**
- `Audio Playback done for <path to .wav> with status: 0`

**Key log patterns (haptic_feedback.log) — bootup periodic health check:**
- `Periodic health: output_state=0, input_gpio=1`
- `USB hub detected`
- `USB_HUB_GPIO_2 value is 1`
- Absence of `nd_get_gpio fail with return -6` and `Periodic health: failed to read input GPIO, error: -6`

**Test cases that validate this flow:**
| Test Case ID     | Python File                                                                                  | What it checks                                               |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `TC_4161`        | `tests/DMS/test_tc_dms_4161_haptic_audio_check.py`                                         | Ignition-off/on cycle → fresh-ignition health check runs (or valid 24h skip) with audio cue, motor OFF confirmation, and HS success report |
| `TC_4162`        | `tests/DMS/test_tc_dms_4162_dms_haptic_check_upon_bootup.py`                               | Bootup periodic health check reports output_state/input_gpio, USB hub detected + GPIO set, no GPIO read errors |

---

## haptic_feedback Critical Info Codes (send_err_msg)

**What this is:** `haptic_feedback` reports significant lifecycle/error events to `haptic_feedback.log` via `send_err_msg` using a fixed set of info codes. **The presence of any of these codes in the log is a signal to flag** — they mark conditions that are not supposed to occur during normal operation (with the exception of the explicit success/skip codes noted below). The agent should treat any of these codes found in `haptic_feedback.log` as a candidate failure and investigate/report accordingly, rather than treating them as routine informational noise.

| Code                                              | Meaning                                                        | Normal or Failure?                                  |
| -------------------------------------------------- | --------------------------------------------------------------- | ---------------------------------------------------- |
| `SM_E_HPTC_LOG_INIT_FAIL`                          | Logger init or config parse failure                              | **Failure** — should not occur                        |
| `SM_E_HPTC_SERIAL_NUM_LOAD_FAIL`                   | Haptic serial number couldn't be loaded from accessories DB      | **Failure** — should not occur                        |
| `SM_E_HPTC_SET_GPIO_FAILED`                        | GPIO set (high or low) API call failed                           | **Failure** — should not occur                        |
| `SM_E_HPTC_GET_GPIO_FAILED`                        | GPIO get (read) API call failed                                  | **Failure** — should not occur                        |
| `SM_E_HPTC_USB_HUB_NOT_CONNECTED`                  | USB Hub not detected at start                                    | **Failure** — should not occur                        |
| `SM_E_HPTC_HEALTH_SUCCESS`                         | Health check passed                                              | Normal (expected, not a failure)                      |
| `SM_E_HPTC_HEALTH_SKIPPED`                         | Health check skipped (not paired or conditions not met)          | Normal/expected depending on config — verify context  |
| `SM_E_HPTC_FAULT_DETECTED`                         | Motor health fault detected                                      | **Failure** — should not occur                        |
| `SM_E_HPTC_FAULT_PERSISTENT`                       | Motor fault persisted after max retries                          | **Failure** — should not occur                        |
| `SM_E_HPTC_NO_HEALTH_48HR`                         | No health check reported in 48+ hours                            | **Failure** — should not occur                        |
| `SM_E_HPTC_TRIGGER_FAILED_REASON_LATENCY`          | Trigger skipped due to latency exceeding threshold                | **Failure** — should not occur                        |
| `SM_E_HPTC_JSON_PARSE_FAIL`                        | JSON parse error or unknown message type                         | **Failure** — should not occur                        |
| `SM_E_HPTC_JSON_SESSION_NAME_MISMATCH`             | Session name mismatch between ZMQ msg and current session        | **Failure** — should not occur                        |
| `SM_E_HPTC_EVENTS_METADATA_WRITE_FAIL`             | Failed to write `haptic_events.json`                              | **Failure** — should not occur                        |

**Where to look:** `haptic_feedback.log` (see Flow 17 log path above).

**Validation guidance:** When analyzing `haptic_feedback.log` for any DMS/haptic test case, grep for the `SM_E_HPTC_*` prefix. If any code other than `SM_E_HPTC_HEALTH_SUCCESS` (and `SM_E_HPTC_HEALTH_SKIPPED` when contextually expected) appears, flag it as an anomaly/failure in the verdict even if the specific test case's own pass criteria don't explicitly check for it — these codes indicate a real fault path was hit.

---

## haptic_feedback Periodic Health Monitoring Log Messages

**What this is:** `periodic_health_monitor` (part of `haptic_feedback`) periodically drives the haptic motor's output GPIO and reads back the input GPIO to confirm the motor responds. It logs each check and, on mismatch, attempts a bounded number of recovery retries before giving up. As with the `SM_E_HPTC_*` codes above, **`LOG_W` (warning) and `LOG_E` (error) lines from this monitor indicate a fault condition and should be flagged** — only the `LOG_I` (info) lines represent normal/expected operation.

| Level  | Log Message                                                                                          | Normal or Failure?                                              |
| ------ | ----------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| LOG_E  | `Periodic health: failed to read input GPIO, error: %d`                                              | **Failure** — GPIO read API call failed                            |
| LOG_I  | `Periodic health: output_state=%d, input_gpio=%d`                                                    | Normal — routine status line                                       |
| LOG_I  | `Periodic health: output HIGH, motor running — normal during alert`                                  | Normal — expected during an active alert                           |
| LOG_W  | `Periodic health: fault detected - output HIGH but motor not responding (input idle)`                | **Failure** — motor not responding while driven                    |
| LOG_W  | `Periodic health recovery (both HIGH): retry %d/%d`                                                  | **Failure path** — recovery attempt in progress                    |
| LOG_E  | `Periodic health recovery (both HIGH): failed to set output LOW, err: %d`                             | **Failure** — recovery step failed                                 |
| LOG_E  | `Periodic health recovery (both HIGH): failed to set output HIGH, err: %d`                            | **Failure** — recovery step failed                                 |
| LOG_E  | `Periodic health recovery (both HIGH): failed to read input GPIO, err: %d`                            | **Failure** — recovery step failed                                 |
| LOG_I  | `Periodic health recovery (both HIGH): motor responding after %d attempt(s)`                          | Normal — recovery succeeded, but indicates a prior fault occurred  |
| LOG_W  | `Periodic health recovery (both HIGH): fault persists after attempt %d/%d (input still HIGH/idle)`    | **Failure** — recovery attempt did not clear the fault              |
| LOG_E  | `Periodic health: motor not responding - fault persistent after %d recovery attempts`                | **Failure** — fault persisted through all retries (terminal)       |
| LOG_W  | `Periodic health: fault detected - both output and input are LOW (motor stuck vibrating)`             | **Failure** — motor stuck vibrating                                 |
| LOG_W  | `Periodic health recovery (both LOW): retry %d/%d`                                                    | **Failure path** — recovery attempt in progress                    |
| LOG_E  | `Periodic health recovery (both LOW): failed to set output HIGH, err: %d`                             | **Failure** — recovery step failed                                 |
| LOG_E  | `Periodic health recovery (both LOW): failed to set output LOW, err: %d`                              | **Failure** — recovery step failed                                 |
| LOG_E  | `Periodic health recovery (both LOW): failed to read input GPIO, err: %d`                             | **Failure** — recovery step failed                                 |
| LOG_I  | `Periodic health recovery (both LOW): fault cleared after %d attempt(s)`                              | Normal — recovery succeeded, but indicates a prior fault occurred  |
| LOG_W  | `Periodic health recovery (both LOW): fault persists after attempt %d/%d (input still LOW/running)`   | **Failure** — recovery attempt did not clear the fault              |
| LOG_E  | `Periodic health: motor stuck vibrating - fault persistent after %d recovery attempts`                | **Failure** — fault persisted through all retries (terminal)       |

**Where to look:** `haptic_feedback.log` (see Flow 17 log path above).

**Validation guidance:** Grep `haptic_feedback.log` for `Periodic health` lines. Treat any `LOG_W`/`LOG_E` occurrence as an anomaly to flag — including transient recovery retries, even if a later `LOG_I` shows the fault eventually cleared, since the underlying `SM_E_HPTC_FAULT_DETECTED` / `SM_E_HPTC_FAULT_PERSISTENT` codes (see Critical Info Codes above) are typically emitted alongside these and the recurrence itself is worth surfacing in the verdict.

---

## haptic_feedback Ignition-ON Health Check Log Messages

**What this is:** `ignition_on_health_check` (guarded by `is_fresh_ignition_start`) determines whether the current boot is a fresh ignition-on event (vs. a software reboot or watchdog reset) and, if so and no health check has run in the last 24 hours, triggers a haptic health check with an audio cue. As with the other haptic log tables above, **`LOG_E` lines indicate a fault and should be flagged**; `LOG_I` lines are normal decision/status tracing for this flow.

| Level  | Log Message                                                                                                     | Normal or Failure?                                              |
| ------ | ------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------- |
| LOG_E  | `get_reset_wake_reason failed, err: unable to read wake reason`                                                   | **Failure** — could not determine wake/reset reason                |
| LOG_I  | `pow_on_off_reason = 0x%X, reason_str = %s`                                                                        | Normal — routine status line                                       |
| LOG_I  | `Wake reason analysis: ignition=%d, sw_reboot=%d, watchdog=%d`                                                     | Normal — routine status line                                       |
| LOG_I  | `Ignition-ON health check skipped (hub=%d, paired=%d, active_monitoring=%d, device_obj=%p)`                       | Normal — expected when preconditions aren't met                    |
| LOG_I  | `Ignition-ON detection: is_fresh_ignition_start=%d`                                                                | Normal — routine status line                                       |
| LOG_I  | `Not a fresh ignition start, skipping ignition-ON health check`                                                   | Normal — expected on sw_reboot/watchdog resets                     |
| LOG_I  | `Ignition-ON: health check skipped (already checked within 24hrs)`                                                | Normal — expected throttling behavior                              |
| LOG_I  | `Playing health check audio: %s`                                                                                  | Normal — routine status line                                       |
| LOG_E  | `Failed to send audio play request for health check`                                                              | **Failure** — audio cue request failed                             |
| LOG_I  | `Ignition-ON: triggering health check (>24hrs since last check)`                                                  | Normal — routine status line                                       |
| LOG_I  | `Ignition-ON: health check trigger completed in %lld ms (expected ~%dms per attempt, max %d retries)`             | Normal — routine status line                                       |

**Where to look:** `haptic_feedback.log` (see Flow 17 log path above).

**Validation guidance:** Grep `haptic_feedback.log` for `Ignition-ON` / `get_reset_wake_reason` / `pow_on_off_reason` / `Wake reason analysis` lines. Flag the two `LOG_E` cases above as anomalies; the rest are informational and describe the fresh-ignition-start decision path.

---

## haptic_feedback 48-Hour No-Health-Check Warning

**What this is:** `check_no_health_48hr_critical_info` periodically checks how long it has been since the last successful haptic health check. If no health check has completed in 48+ hours, it logs a warning — this corresponds to the `SM_E_HPTC_NO_HEALTH_48HR` critical info code (see Critical Info Codes above) and should be flagged as an anomaly, since it indicates the health monitoring loop has stalled or the motor/health path is not functioning.

| Level  | Log Message                                              | Normal or Failure?                                    |
| ------ | ----------------------------------------------------------- | --------------------------------------------------------- |
| LOG_W  | `No haptic health check in 48+ hours (%lld ms ago)`        | **Failure** — health monitoring has stalled for 48+ hours |

**Where to look:** `haptic_feedback.log` (see Flow 17 log path above).

**Validation guidance:** Grep `haptic_feedback.log` for `No haptic health check in 48` — any match should be flagged as a failure and cross-checked against `SM_E_HPTC_NO_HEALTH_48HR` in the same log.

---

## Config-Driven Flow Activation

The agent MUST read the device config from `device_data/device_<ID>_config.ini`
before selecting test cases. Use the following mapping:

| Config Section      | Config Key           | Value          | Activates Flow(s)                               | Test Cases Affected                                                        |
| ------------------- | -------------------- | -------------- | ----------------------------------------------- | -------------------------------------------------------------------------- |
| `[dms_drowsy]`      | `enabled`            | `1` / `true`   | Flows 1–11, 13–15                               | All DMS recording tests                                                    |
| `[dms_camera]`      | `enabled`            | `true`         | Flow 1, 2 (camera enabled even if drowsy=0)     | `TC_2169`, `TC_2174`                                                       |
| `[camera]`          | `dms_ld_enabled`     | `true`         | Flow 6 (LD file size/property), Flow 9 (LPM)   | `TC_1806`, `TC_2757`, `TC_2768`, `TC_2813`, `TC_2814`, `TC_2818`, `TC_2840` |
| `[Transcode]`       | `store_lq_dms_file`  | `false`        | Flow 10 (LQ deletion), Flow 11 (alert cap)      | `TC_2823`, `TC_2824`, `TC_2836`                                            |
| `[Transcode]`       | `store_lq_dms_file`  | `true`         | Flow 10 (LQ retention count)                    | `TC_2835`                                                                  |
| `[Transcode]`       | `num_dms_videos_max` | `<integer>`    | Flow 11 (alert cap ceiling)                     | `TC_2836`                                                                  |
| `[privacy_mode]`    | `enhanced_privacy`   | `true`         | Flow 8 (enhanced privacy suppresses DMS)        | `TC_2812`                                                                  |
| `[privacy_mode]`    | `inward`             | `true`         | Flow 8 (full inward privacy, DMS stops)         | `TC_2813`                                                                  |
| `[privacy_mode]`    | `inward`             | `true` + `enhanced_privacy=false` | Flow 8 (partial privacy)     | `TC_2804`                                                                  |
| `[privacy_mode_activate]` | `ignition_based` | `true`       | Flow 8 (ignition-based privacy activate)        | `TC_2175`, `TC_2813`, `TC_2814`                                            |
| `[privacy_mode_deactivate]` | `ignition_based` | `true`     | Flow 8 (ignition-based privacy deactivate)      | `TC_2175`                                                                  |
| `[haptic_feedback]` | `enabled`            | `1`            | Flow 17 (haptic service active), Flow 18 (health checks) | `TC_4157`, `TC_3801`, `TC_4161`, `TC_4162`                          |
| `[haptic_motor_health_monitoring]` | `is_active_monitoring_enabled` | `true` | Flow 18 (ignition-on audio check, bootup GPIO check) | `TC_4161`, `TC_4162`                                          |
| `[healthstats]`     | `videohealthstats_secs` | `<integer>` | Flow 14 (HS payload cadence)                    | `TC_2850`, `TC_3778`, `TC_3779`, `TC_3793`, `TC_3801`                     |
| —                   | —                    | —              | Flows 16 (service status — always active)       | `TC_520`, `TC_524`, `TC_534`, `TC_541`, `TC_2830`, `TC_2831`, `TC_4159`   |

**Rules:**
- Flows marked "always active" → run their test cases unconditionally (service status flows)
- `dms_drowsy=1` is the primary gate — most flows require it; check device config first
- `dms_ld_enabled=true` is required for LD file size/property/LPM tests
- Privacy-mode tests require specific `[privacy_mode]` + `[privacy_mode_activate/deactivate]` config
- `haptic_feedback` tests require `[haptic_feedback] enabled=1` in config
- All DMS recording tests skip on non-`bagheera3`/`octo` device types — check `device_type` before running

---

## Cross-Service Dependencies

| Related Service         | Why                                                                              | When to check its logs               |
| ----------------------- | -------------------------------------------------------------------------------- | ------------------------------------ |
| `bagheera` (ndcentral)  | Owns DMS camera session lifecycle, IRLED health, session metadata JSON           | All flows involving cam_num=8        |
| `cam_rec`               | Camera recording process — enables/disables ADD, receives DMS config, LED msgs  | Flows 1, 2, 5, 8, 15                 |
| `analytics`             | Sends frames to HS (`Cam 8 : sending msg`) and frames to dmsAnalyticsClient     | Flow 4                               |
| `dmsAnalyticsClient`    | NRT/RT analytics, writes obsdata, sends health stats message                     | Flows 3, 4, 14                       |
| `inference`             | Hosts NRT inference pipeline (`Starting DMS NRT`)                               | Flow 3                               |
| `circular_buffer`       | Stores DMS LD files in DB, enforces `num_dms_videos_max` cap                    | Flows 10, 11, 12, 13                 |
| `service_mon`           | Monitors all DMS-related service start/stop/error events                         | Flow 16, 17                          |
| `haptic_feedback`       | Delivers haptic motor alerts; writes per-session `haptic_events.json`            | Flow 17, 18                          |
| `audio`                 | Plays the ignition-on haptic health-check audio cue                              | Flow 18                              |
| `HealthStatsManager`    | Receives health stats from dmsAnalyticsClient; publishes HS payload              | Flows 14, 15, 17                     |
| `otacheck`              | Downloads cloud overrides that activate/deactivate DMS features                  | Flow 2 (cloud override tests)        |
| `power_monitor`         | Orchestrates crank-off, LPW; DMS sessions must close cleanly before shutdown     | Flow 9                               |

---

## Flow Dependency Graph

```
boot → [Flow 2: Config Logic] → dms_drowsy/dms_camera enabled?
  └─ YES → [Flow 1: Camera Start] → cam_rec enables DMS cam 8
               → [Flow 4: RT Analytics] → frames → dmsAnalyticsClient
               → [Flow 3: NRT Analytics] → per session → obsdata written
               → [Flow 5: Session Metadata] → dmsStartTime/Ld embedded in JSON
               → [Flow 15: IRLED Health] → irled_status reported per session
               → [Flow 6: LD File] (if dms_ld_enabled=true) → .mp4.ld.mp4 to SD card
               → [Flow 14: Health Stats] → dms keys in HS payload + obs file
  └─ Privacy ON → [Flow 8: Privacy Interactions] → DMS paused/resumed
  └─ Reboot mid-session → [Flow 7: Partial Session] → move_partial_files
  └─ Crank OFF → [Flow 9: LPM] → session closed before LPW

config `[Transcode] store_lq_dms_file` → [Flow 10: LQ File Behavior]
alert accumulation → [Flow 11: Alert Cap] → CB enforces ≤80 alert LD files
[Flow 12: Zero-MB Deletion] → on every boot (unconditional)
[Flow 13: Video List Update] → circ_buff rotation → added/deleted file list

[Flow 16: Service Status] → always active → service_mon monitors all DMS processes
cloud OTA → [Flow 2: Config Logic] → otacheck applies → bagheera restarts

haptic_feedback enabled → [Flow 17: Haptic] → per session haptic_events.json
                        → [Flow 18: Haptic Health Checks] → ignition-on audio check (throttled 24h) + bootup GPIO check
```

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Check device type** — skip all DMS recording tests if device is NOT `bagheera3` or `octo`; service-status tests (TC_520, TC_524, TC_534, TC_541, TC_2830, TC_2831, TC_4159) run on any device type; TC_4161/TC_4162 (haptic health checks) require `bagheera3` specifically
3. **Determine active flows** using the Config-Driven Flow Activation table above
4. **For each active flow**, read the mapped pytest test case files from `tests/DMS/`
5. **From each test file**, use docstrings (`"""STEP N — ..."""`) to understand what each step checks and which log pattern to search
6. **Log file paths** (bagheera3 / octo):
   - `ndcentral`: `/home/ubuntu/.nddevice/log/ndcentral/*`
   - `cam_rec`: `/home/ubuntu/.nddevice/log/cam_rec/*`
   - `dmsAnalyticsClient`: `/home/ubuntu/.nddevice/log/dmsAnalyticsClient/*`
   - `analytics`: `/home/ubuntu/.nddevice/log/analytics/*`
   - `inference`: `/home/ubuntu/.nddevice/log/inference/*`
   - `service_mon`: `/home/ubuntu/.nddevice/log/service_mon/*`
   - `circular_buffer DB`: `/home/ubuntu/.nddevice/circular_buffer.db`
   - `SD card`: `/media/data/nd_sdcard/`
   - `ND_OUTPUT`: `/home/iriscli/ND_OUTPUT/`
   - `HS backup`: `/home/ubuntu/.nddevice/log/health/backup/*gz`
   - `observations`: `/home/ubuntu/.nddevice/observations/*`
   - `haptic session events`: `/home/ubuntu/autocam/<session>/haptic_events.json`
7. **Search device logs** in `device_logs/<device_id>/` using patterns from the flow sections above
8. **For IRLED health tests** (TC_3778, TC_3779, TC_3793): block API calls first (`control_api_calls`), wait for HS payload gz, decompress, then grep for required keys
9. **For cloud override tests** (TC_2169, TC_2173, TC_2174, TC_2844): `otacheck` log confirms the override name was received
10. **For any haptic_feedback log analysis**: grep `haptic_feedback.log` for `SM_E_HPTC_*` codes (see "haptic_feedback Critical Info Codes" above) and flag any code besides `SM_E_HPTC_HEALTH_SUCCESS`/`SM_E_HPTC_HEALTH_SKIPPED` as an anomaly; also grep for `Periodic health` lines (see "haptic_feedback Periodic Health Monitoring Log Messages" above) and flag any `LOG_W`/`LOG_E` occurrence; also grep for `Ignition-ON`/`get_reset_wake_reason`/`pow_on_off_reason` lines (see "haptic_feedback Ignition-ON Health Check Log Messages" above) and flag the `LOG_E` cases; also grep for `No haptic health check in 48` (see "haptic_feedback 48-Hour No-Health-Check Warning" above) and flag any match
11. **For TC_4161** (ignition-on audio check): both the full health-check log sequence AND either 24h-skip log are valid PASS outcomes — only fail if neither path's logs appear; also check `audio.log` for `Audio Playback done ... with status: 0`
12. **For TC_4162** (bootup GPIO check): confirm absence of GPIO error patterns is itself a required assertion, not just presence of the periodic health/USB hub logs
13. **Test case IDs have been renumbered** — some pytest files were renamed without changing test logic: `TC_3800`→`TC_4157`, `TC_3803`→`TC_4159`, `TC_3804`→`TC_4160`. Always resolve test cases by the current filename in `tests/DMS/`, not by a remembered numeric ID, since IDs may shift again
14. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED / NA (device type mismatch)
