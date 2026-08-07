---
name: audioplayback-service-validation
description: "Use when: validating audioPlayback service behavior from device logs. Covers service initialization, alert audio playback (all alert types), session serialization, speaker volume config (speaker_v2/legacy), unsupported value rejection, volume persistence after svc reboot, volume after bagheera crash, cloud config push, and audio file playability."
argument-hint: "device ID (e.g., /audioplayback-service-validation 103432407294)"
---

# audioPlayback (`audioPlayback`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the audioPlayback service —
> what it does, how its flows relate to each other, and which config keys activate which flows.
> The agent reads pytest test cases in `tests/audioplayback/` for actual log patterns and
> acceptance criteria — this skill does NOT duplicate those.

---

## Service Overview

`audioPlayback` (process: `audioPlayback`) is the service responsible for playing alert audio
files on the device speaker in response to events from bagheera (nd-central). It receives audio
play requests via message queue, plays the specified `.wav` file, serializes audio event data
per session into `audio_events.pb` protobuf files, and sends a response back to the caller
with a result code (`response: 0` = success, `response: 1` = user alert acknowledged).
The service reads its speaker configuration from `bagheera_config.ini` / `bagheera_override.ini`
via bagheera (nd-central), not directly.

**Process name:** `audioPlayback`
**Log file:** `audio.log` (path: `/home/ubuntu/.nddevice/log/audio/`)
**Log tag prefix:** `Audio:`, `AUDIO:`, `CFG_PRSR:`, `SUTILS:`, `MSGQ:`, `MSGQ_U:`
**Primary config sections:** `[speaker_v2]`, `[speaker]`, `[inCabFeedback]`, `[privacy_mode]`, `[camera]`
**Output dir:** `/home/iriscli/ND_OUTPUT/`

---

## Log Format

audioPlayback log lines follow this format:
```
<epoch_ms>: <uptime_ms>: <TAG>: <level>: <pid>: <tid>: <message>
```

Key tags observed in device logs:
- `Audio:` — main service logic (startup, session changes, message received/serialized, latency)
- `AUDIO:` — audio response logic: `send_audio_play_response called with alert_type: <type>, audio_file: <path>, response: <N>`
- `CFG_PRSR:` — config parsing at startup: override file present/parsed
- `SUTILS:` — signal registration at startup
- `MSGQ:` / `MSGQ_U:` — message queue setup (SM 20 error is normal/harmless at startup)

**Log routing:** Logs rotate every 30 minutes via `*** Automatic Routing ***` lines into
`log_<epoch>.log` files. All audio logs live under `/home/ubuntu/.nddevice/log/audio/`.

---

## Service Flows

### Flow 1: Service Initialization

**What happens:** At startup, the service registers signals (`SUTILS`), parses the override
config (`CFG_PRSR`), attempts to set up the message queue (SM 20 error is expected and
harmless), sets the output directory to `/home/iriscli/ND_OUTPUT`, then enters the message
wait loop. A new PID is assigned on each restart.

**When active:** At every service start / restart / reboot
**Frequency:** Once per boot / service restart

**Key log patterns:**
```
Audio: ... ######## Starting Audio Service on #####
SUTILS: ... Signals registered
CFG_PRSR: ... Override file /home/ubuntu/config/bagheera_override.ini present
CFG_PRSR: ... OVerride file parsed successfully
MSGQ: E: ... Error creating token SM 20, Error msg: No such file or directory   (normal)
Audio: ... Output dir: /home/iriscli/ND_OUTPUT
Audio: ... Waiting for messages
```

**Test cases that validate this flow:**
| Test Case ID     | pytest Path                                                              | What it checks                                    |
| ---------------- | ------------------------------------------------------------------------ | ------------------------------------------------- |
| `TC_2697`        | `tests/audioplayback/test_tc_2697_speaker_v2_volume_after_svc_reboot.py` | Service re-init log after svc reboot              |
| `TC_2699`        | `tests/audioplayback/test_tc_2699_speaker_v2_volume_after_bagheera_crash.py` | Service re-init after bagheera crash          |

---

### Flow 2: Alert Audio Playback (System Alerts)

**What happens:** When bagheera detects an alert event (cw, tl, tlffc, rbw, ts, distracted,
sb, pcw, rt_tg_cs, inertial, etc.), it sends an audio play message to audioPlayback. The
service logs the session, filename, message latency (ms), and sends a response with
`response: 0` (played). Alert type → WAV file mappings observed from device logs:

| Alert Type    | WAV File                               |
| ------------- | -------------------------------------- |
| `cw`          | `fcw.wav` / `moderate_cw.wav`         |
| `tl`          | `green.wav`                            |
| `tlffc`       | `green.wav`                            |
| `rbw`         | `road_boundary_warning.wav`            |
| `ts`          | `overspeed.wav`                        |
| `distracted`  | `distracted.wav`                       |
| `sb`          | (seat belt warning)                    |
| `pcw`         | (pedestrian collision warning)         |
| `rt_tg_cs`    | (right turn / going straight)          |
| `inertial`    | (inertial event)                       |

**When active:** Whenever an alert event fires during an active session
**Frequency:** Event-driven; multiple per session
**Cross-service impact:** bagheera (nd-central) sends the request; response goes back to bagheera

**Key log patterns:**
```
Audio: ... Audio message received for session: <session_id>
Audio: ... Filename: /home/ubuntu/autocam/audio/nd_debug2/<alert>.wav
Audio: ... Message latency = <N> ms
AUDIO: ... send_audio_play_response called with alert_type: <type>, audio_file: <path>, response: 0
```

**Test cases that validate this flow:**
| Test Case ID     | pytest Path                                                              | What it checks                                    |
| ---------------- | ------------------------------------------------------------------------ | ------------------------------------------------- |
| `TC_2607`        | `tests/audioplayback/test_tc_2607_speaker_v2_default_audio_level_with_mic_enabled.py` | Full playback chain via TC_2591 helper |
| `TC_2684`        | `tests/audioplayback/test_tc_2684_speaker_v2_audio_level_master.py`      | Playback at each speaker_v2 volume level (1–5)   |
| `TC_2686`        | `tests/audioplayback/test_tc_2686_speaker_volume_6_to_10_with_default_fraction_master.py` | Legacy speaker levels 6–10           |
| `TC_2690`        | `tests/audioplayback/test_tc_2690_speaker_volume_6_to_10_with_non_default_fraction_master.py` | Legacy non-default fraction      |
| `TC_2697`        | `tests/audioplayback/test_tc_2697_speaker_v2_volume_after_svc_reboot.py` | Alert playback after svc reboot                   |
| `TC_2699`        | `tests/audioplayback/test_tc_2699_speaker_v2_volume_after_bagheera_crash.py` | Alert playback after bagheera crash           |

---

### Flow 3: User Alert Playback (TC_2591 sub-flow)

**What happens:** When a user manually triggers an alert (via button press or TCP port 12347),
bagheera sends an audio play request for `user_alert_triggered.wav`. The full chain:
1. bagheera: `send alert audio play with uuid`
2. bagheera: `Audio request added for file <path>`
3. bagheera: `User audio alert sent`
4. audioPlayback: `Audio message received for session`
5. audioPlayback: `Filename: <path>`
6. audioPlayback: `Audio Playback done for <path> with status: 0`
7. audioPlayback: `send_audio_play_response called with alert_type: UserAl..., response: 1`
8. audioPlayback: `Sending audio playback response: file=<path>, response=1, sender=q_audioplayback`
9. bagheera: `audio_msg.*file: <path>`
10. bagheera: `Removing audio request for file <path>`

**Key difference from system alerts:** `response: 1` (user acknowledged) vs `response: 0` (system played).

**Audio file path by device type:**
- bagheera2 / bagheera3: `/home/ubuntu/autocam/audio/nd_debug2/user_alert_triggered.wav`
- krait / krait2: `/data/nd_files/autocam/audio/nd_debug2/user_alert_triggered.wav`

**When active:** On user alert button press / TCP alert injection
**Frequency:** Event-driven

**Key log patterns:**
```
Audio: ... Audio message received for session: <session_id>
Audio: ... Filename: /home/ubuntu/autocam/audio/nd_debug2/user_alert_triggered.wav
Audio: ... Audio Playback done for <path> with status: 0
AUDIO: ... send_audio_play_response called with alert_type: UserAl..., audio_file: <path>, response: 1
Audio: ... Sending audio playback response: file=<path>, response=1, sender= q_audioplayback
```

**Test cases that validate this flow:**
| Test Case ID     | pytest Path                                                              | What it checks                                    |
| ---------------- | ------------------------------------------------------------------------ | ------------------------------------------------- |
| `TC_2607`        | `tests/audioplayback/test_tc_2607_speaker_v2_default_audio_level_with_mic_enabled.py` | Full TC_2591 user-alert chain         |
| `TC_2684`        | `tests/audioplayback/test_tc_2684_speaker_v2_audio_level_master.py`      | TC_2591 as child within master                    |
| `TC_2697`        | `tests/audioplayback/test_tc_2697_speaker_v2_volume_after_svc_reboot.py` | TC_2591 user-alert chain after svc reboot         |

---

### Flow 4: Session Serialization (~60s interval)

**What happens:** Every ~60 seconds (session boundary), audioPlayback detects a session change
and serializes all audio events for the completed session into a `.pb` (protobuf) file at
`/home/iriscli/ND_OUTPUT/<session_id>/audio_events.pb`.

**When active:** Always (during active trip sessions)
**Frequency:** Every ~60 seconds on session boundary

**Key log patterns:**
```
Audio: ... Session change detected
Audio: ... Audio data successfully serialized for session: <session_id> in the file /home/iriscli/ND_OUTPUT/<session_id>/audio_events.pb
```

**Test cases that validate this flow:**
| Test Case ID     | pytest Path                                                              | What it checks                                    |
| ---------------- | ------------------------------------------------------------------------ | ------------------------------------------------- |
| `TC_3487`        | `tests/audioplayback/test_tc_3487_verify_files_playable.py`              | Audio files exist and are playable on device      |

---

### Flow 5: Speaker V2 Volume Configuration

**What happens:** bagheera (nd-central) reads `[speaker_v2] volume` from config at startup.
Valid range: **1–5**. The value is logged as `Speaker Level from config: <N>`. The speaker
hardware is initialized and `Speaker init successful` is logged. service_mon receives the
volume as `Vol: <N>`.

**When active:** Only when `[speaker_v2]` section is present in config
**Frequency:** Once per bagheera restart / reboot

**Key log patterns** (in ndcentral logs):
```
Speaker Level from config: <1-5>
Speaker init successful
```
**service_mon logs:**
```
Vol: <N>
```

**Test cases that validate this flow:**
| Test Case ID     | pytest Path                                                              | What it checks                                    |
| ---------------- | ------------------------------------------------------------------------ | ------------------------------------------------- |
| `TC_2607`        | `tests/audioplayback/test_tc_2607_speaker_v2_default_audio_level_with_mic_enabled.py` | Default volume from bagheera_config.ini  |
| `TC_2684`        | `tests/audioplayback/test_tc_2684_speaker_v2_audio_level_master.py`      | Volume levels 1–5 each verified                  |
| `TC_2696`        | `tests/audioplayback/test_tc_2696_speaker_v2_unsupported_values_rejected.py` | Unsupported values rejected, fallback to default |
| `TC_2697`        | `tests/audioplayback/test_tc_2697_speaker_v2_volume_after_svc_reboot.py` | Volume persists after svc reboot                  |
| `TC_2699`        | `tests/audioplayback/test_tc_2699_speaker_v2_volume_after_bagheera_crash.py` | Volume persists after bagheera crash          |
| `TC_2782`        | `tests/audioplayback/test_tc_2782_speaker_v2_vehicle_level_cloud_config_push.py` | Volume applied from cloud config push      |

---

### Flow 6: Legacy Speaker Volume Configuration (Levels 6–10)

**What happens:** When `[speaker]` section has `volume = 6–10`, bagheera reads both `volume`
and `fraction` (device-type-dependent default). The value is logged as
`Speaker Volume from config: <N>` and `Speaker Fraction from config: <F>`.
Device-type-specific volume apply log:
- krait/krait2: `Setting speaker volume according to level/volume config <value>`
- bagheera3: `Overriding volume as config is present <value>`

**Default fraction by device type:**
| Device Type      | Default Fraction | Non-Default Fraction |
| ---------------- | ---------------- | -------------------- |
| bagheera2/3      | 5                | 31                   |
| krait2           | 0                | 7                    |
| krait            | 2                | 7                    |

**When active:** Only when `[speaker]` section present with `volume = 6–10`
**Frequency:** Once per bagheera restart

**Key log patterns** (in ndcentral logs):
```
Speaker Volume from config: <6-10>
Speaker Fraction from config: <fraction>
Setting speaker volume according to level/volume config <value>   (krait/krait2)
Overriding volume as config is present <value>                     (bagheera3)
Speaker init successful
```

**Test cases that validate this flow:**
| Test Case ID     | pytest Path                                                              | What it checks                                    |
| ---------------- | ------------------------------------------------------------------------ | ------------------------------------------------- |
| `TC_2686`        | `tests/audioplayback/test_tc_2686_speaker_volume_6_to_10_with_default_fraction_master.py` | Levels 6–10 with default fraction   |
| `TC_2690`        | `tests/audioplayback/test_tc_2690_speaker_volume_6_to_10_with_non_default_fraction_master.py` | Levels 6–10 with non-default fraction |

---

### Flow 7: Unsupported Volume Rejection

**What happens:** When `[speaker_v2] volume` is set to an invalid value (`0`, `10`, `-1`,
empty, `$@#`, `STX`), bagheera logs:
`Speaker level not in range setting volume according to default level`
and falls back to the default volume from `bagheera_config.ini`.

**When active:** When `[speaker_v2] volume` value is outside range 1–5
**Frequency:** Once per bagheera restart with bad config

**Key log patterns** (in ndcentral logs):
```
Speaker level not in range setting volume according to default level
Speaker Level from config: <default_fallback_value>
```

**Test cases that validate this flow:**
| Test Case ID     | pytest Path                                                              | What it checks                                    |
| ---------------- | ------------------------------------------------------------------------ | ------------------------------------------------- |
| `TC_2696`        | `tests/audioplayback/test_tc_2696_speaker_v2_unsupported_values_rejected.py` | Each of: 10, 0, $@#, STX, -1, "" rejected  |

---

### Flow 8: Volume Persistence After SVC Reboot

**What happens:** When bagheera is killed (`sudo systemctl stop bagheera.service`), svc
detects keepalive timeout and triggers a device reboot. After the reboot, bagheera reads
the same `[speaker_v2] volume` config and re-initializes the speaker at the same level.

**Sequence:**
1. Kill bagheera → svc logs `Keep alive timeout: bagheera diff`
2. Device reboots (tracked via `device.track_reboot`)
3. After reboot: `Speaker Level from config: <N>` in ndcentral
4. TC_2591 user-alert playback chain runs to confirm audio works

**When active:** When bagheera is killed/stopped (svc reboot scenario)
**Frequency:** Event-driven

**Key log patterns** (svc logs):
```
Keep alive timeout:  bagheera diff
```
**power_mon logs:**
```
ka_minified.*"ignition":1,"previousShutdownReason":"DBSTATE_SHUTDOWN_SVC:REBOOT
```

**Test cases that validate this flow:**
| Test Case ID     | pytest Path                                                              | What it checks                                    |
| ---------------- | ------------------------------------------------------------------------ | ------------------------------------------------- |
| `TC_2697`        | `tests/audioplayback/test_tc_2697_speaker_v2_volume_after_svc_reboot.py` | Volume same after full svc reboot                 |

---

### Flow 9: Volume Persistence After Bagheera Crash

**What happens:** When bagheera is killed with SIGABRT (`kill -6 <pid>`), service_mon
logs `Service error: NDC :` and restarts bagheera. After restart (+ audioPlayback + svc
restart), the speaker re-initializes at the configured volume level.

**Sequence:**
1. Kill bagheera SIGABRT → service_mon: `Service error: NDC :`
2. Manually restart: `bagheera`, `svc`, `audioPlayback`
3. Wait 20s for speaker init
4. Verify `Speaker Level from config: <N>` in ndcentral
5. Run TC_2591 user-alert chain

**When active:** When bagheera crashes / is killed with SIGABRT
**Frequency:** Event-driven

**Key log patterns** (service_mon logs):
```
Service error: NDC :
```

**Test cases that validate this flow:**
| Test Case ID     | pytest Path                                                              | What it checks                                    |
| ---------------- | ------------------------------------------------------------------------ | ------------------------------------------------- |
| `TC_2699`        | `tests/audioplayback/test_tc_2699_speaker_v2_volume_after_bagheera_crash.py` | Volume same after bagheera crash + restart    |

---

### Flow 10: Cloud Config Push — Speaker Volume

**What happens:** The IDMS staging API is used to push `[speaker_v2] volume` config to the
device. After push, `otacheck_count.txt` is set to `79` (or `echo 79 | tee`) to trigger
immediate OTA sync. Config takes effect after bagheera restart. TC_2782 validates each of
volumes 1–5 via separate push+restart+verify cycles.

**OTA trigger paths by device type:**
- bagheera2/3: `/dev/shm/nd_files_c/otacheck_count.txt`
- krait: `/dev/shm/otacheck_count.txt`

**Wait time after OTA trigger:** 240 seconds for config to sync.

**When active:** When cloud config push is used (staging IDMS API)
**Frequency:** Event-driven (test scenario)
**Cross-service impact:** IDMS → bagheera_override.ini updated → bagheera restart required

**Test cases that validate this flow:**
| Test Case ID     | pytest Path                                                              | What it checks                                    |
| ---------------- | ------------------------------------------------------------------------ | ------------------------------------------------- |
| `TC_2782`        | `tests/audioplayback/test_tc_2782_speaker_v2_vehicle_level_cloud_config_push.py` | Volumes 1–5 each pushed via cloud API     |

---

### Flow 11: Audio File Playability Check

**What happens:** Verifies all expected audio `.wav` files for the device variant are present
and playable using `aplay`. CSV manifests list expected files by device type and region.
`aplay` exit code 0 = playable; non-zero = corrupt/missing.

**CSV manifest paths:** `claude_device_validator/assets/audio/`
- `D450_D430_US_audio.csv`, `D450_D430_IN_audio.csv`
- `D210_D215_US_audio.csv`, `D210_D215_IN_audio.csv`, `D210_global_audio.csv`

**CSV columns:** `filepath, filename, md5sum, filesize, channels, bit_rate, samp_freq`

**aplay command by device type:**
- bagheera/bagheera3/D450: `sudo -S aplay "<filepath>/<filename>" >/dev/null 2>&1`
- krait/D210: `aplay "<filepath>/<filename>" >/dev/null 2>&1`

**When active:** Always (standalone audit of audio file assets)
**Frequency:** Once per test run

**Test cases that validate this flow:**
| Test Case ID     | pytest Path                                                              | What it checks                                    |
| ---------------- | ------------------------------------------------------------------------ | ------------------------------------------------- |
| `TC_3487`        | `tests/audioplayback/test_tc_3487_verify_files_playable.py`              | All CSV-listed audio files exist and are playable |

---

### Flow 12: Audio Recording with Mic Enabled

**What happens:** When `[privacy_mode] audio = false` and `[camera] audio_enable = true` +
`audio_encryption = true`, bagheera records in-cab audio per session. Sessions are encoded
via `ffmpeg` to `.aac`, copied to sdcard, and uploaded via unifieduploader.

**Key log patterns** (in ndcentral logs):
```
audio recording is enabled
creating folder for session <session_name>
Audio encode cmd: ffmpeg.*<session>.aac
File copy successful from.*<session>.aac to <sd_card_path>/<session>.aac
```
**unifieduploader logs:**
```
Upload successful for video: <sd_card_path>/<session>.mp4
Deleted.*<session>.aac
```

**When active:** When `[camera] audio_enable = true` and `[privacy_mode] audio = false`
**Frequency:** Per session
**Cross-service impact:** unifieduploader uploads and deletes `.aac` files

**Test cases that validate this flow:**
| Test Case ID     | pytest Path                                                              | What it checks                                    |
| ---------------- | ------------------------------------------------------------------------ | ------------------------------------------------- |
| `TC_2607`        | `tests/audioplayback/test_tc_2607_speaker_v2_default_audio_level_with_mic_enabled.py` | Audio recording enabled, .aac created/uploaded |

---

## Config-Driven Flow Activation

The agent MUST read device config from `device_data/device_<ID>_config.ini` before
selecting flows and test cases:

| Config Section    | Config Key          | Value / Condition            | Activates Flow(s)                          | Test Cases Affected                        |
| ----------------- | ------------------- | ---------------------------- | ------------------------------------------ | ------------------------------------------ |
| `[speaker_v2]`    | `volume`            | `1–5` (valid)                | Flow 5: Speaker V2 Volume                  | TC_2607, TC_2684, TC_2697, TC_2699, TC_2782 |
| `[speaker_v2]`    | `volume`            | `0`, `10`, `-1`, special     | Flow 7: Unsupported Rejection              | TC_2696                                    |
| `[speaker]`       | `volume`            | `6–10`                       | Flow 6: Legacy Speaker Volume              | TC_2686, TC_2690                           |
| `[speaker]`       | `fraction`          | device-type dependent        | Flow 6: Legacy Speaker (fraction)          | TC_2686 (default), TC_2690 (non-default)   |
| `[inCabFeedback]` | `enable`            | `1`                          | Flow 2 & 3: Alert Playback (required)      | TC_2607, TC_2684, TC_2697, TC_2699         |
| `[privacy_mode]`  | `audio`             | `false`                      | Flow 12: Audio Recording with Mic          | TC_2607                                    |
| `[camera]`        | `audio_enable`      | `true`                       | Flow 12: Audio Recording with Mic          | TC_2607                                    |
| —                 | —                   | always                       | Flow 1: Service Init                       | TC_2697, TC_2699                           |
| —                 | —                   | always (when session active) | Flow 4: Session Serialization              | TC_3487                                    |
| —                 | cloud API push      | IDMS staging push            | Flow 10: Cloud Config Push                 | TC_2782                                    |

**Default values** (when key is absent from config):
- `[speaker_v2] volume` → `3` (typical device default)
- `[speaker] fraction` → device-type dependent (see Flow 6 table)
- `[inCabFeedback] enable` → `0` (disabled unless explicitly set)
- `[privacy_mode] audio` → `true` (privacy on by default)
- `[camera] audio_enable` → `false`

**Rules:**
- `[inCabFeedback] enable = 1` is required for alert playback tests (Flows 2, 3) — skip if absent
- Flow 7 (unsupported rejection) requires deliberately setting a bad value — only in TC_2696
- TC_2782 requires IDMS staging API access — skip if device is not registered in IDMS staging
- Flows 5 and 6 are mutually exclusive: `[speaker_v2]` takes precedence over `[speaker]` when both present

---

## Cross-Service Dependencies

| Related Service      | Why                                                                     | When to check its logs              |
| -------------------- | ----------------------------------------------------------------------- | ----------------------------------- |
| `bagheera` (ndcentral) | Sends audio play requests; reads speaker config; logs volume/init     | Flows 2, 3, 5, 6, 7, 9, 12          |
| `service_mon`        | Detects bagheera crash (`Service error: NDC :`)                        | Flow 9 (crash recovery)             |
| `svc`                | Detects keepalive timeout after bagheera kill → triggers reboot        | Flow 8 (svc reboot)                 |
| `power_mon`          | `ka_minified` log shows `previousShutdownReason` after svc reboot      | Flow 8 (shutdown reason verify)     |
| `unifieduploader`    | Uploads `.aac` audio files, then deletes them from sdcard              | Flow 12 (audio recording)           |
| `IDMS staging API`   | Cloud config push for speaker_v2 volume via `audioplayback_cloud_config.py` | Flow 10 (cloud config push)     |

---

## Flow Dependency Graph

```
boot → [Flow 1: Service Init] → Waiting for messages
     → [Flow 5: Speaker V2 Config] (if speaker_v2 present) — once per bagheera restart
     → [Flow 6: Legacy Speaker Config] (if speaker vol 6-10) — once per bagheera restart
     → [Flow 7: Unsupported Rejection] (if bad volume value) — once per bagheera restart

session active → [Flow 4: Session Serialization] — every ~60s
alert event → [Flow 2: Alert Audio Playback] → response: 0
user press → [Flow 3: User Alert Playback] → response: 1

bagheera kill (stop) → svc timeout → reboot → [Flow 8: Volume Persistence Post SVC Reboot]
bagheera kill (SIGABRT) → service_mon restart → [Flow 9: Volume Persistence Post Crash]
cloud API push → OTA sync (240s) → bagheera restart → [Flow 10: Cloud Config Push]

mic enabled + privacy off → [Flow 12: Audio Recording with Mic] → ffmpeg → .aac → sdcard → upload

audio asset audit → [Flow 11: File Playability Check] (standalone, no service interaction)
```

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Determine active flows** using the Config-Driven Flow Activation table above
3. **For Flows 2/3** (`[inCabFeedback] enable != 1`): skip alert playback test cases
4. **For each active flow**, read the corresponding pytest files from `tests/audioplayback/`
5. **Log file for audioPlayback events**: `/home/ubuntu/.nddevice/log/audio/` — use `Audio:` and `AUDIO:` tags
6. **Log file for speaker config and init**: `/home/ubuntu/.nddevice/log/ndcentral/` — use `Speaker Level/Volume/Fraction` patterns
7. **Log file for crash detection**: `/home/ubuntu/.nddevice/log/service_mon/` — use `Service error: NDC :`
8. **Log file for svc reboot**: `/home/ubuntu/.nddevice/log/svc/` — use `Keep alive timeout: bagheera diff`
9. **Alert playback response codes**: `response: 0` = system alert played; `response: 1` = user alert acknowledged. Both are SUCCESS.
10. **Message latency**: values in the log (`Message latency = N ms`) should be < 500ms; > 1000ms is a warning signal
11. **Session serialization**: verify `audio_events.pb` files appear under `/home/iriscli/ND_OUTPUT/<session_id>/`
12. **Audio file playability (TC_3487)**: match device type + region to the correct CSV manifest, then verify each file with `aplay`
13. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED / SKIPPED
