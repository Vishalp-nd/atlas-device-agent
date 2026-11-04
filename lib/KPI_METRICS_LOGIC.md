# KPI Summary Metrics: Calculation Logic

How the OTA report's KPI Summary tables are computed from the device logs: the log line
behind each KPI, and how its value is calculated. Logic only. The code is
`lib/kpi_metrics.py` (`KPI_LOG_LINES` = the log patterns, `KPI_TABLES` = the metrics),
`lib/scanners/kpi.py` (the day scan) and `_kpi_sections()` in `lib/ota_report.py` (the tables).

The metric and log line sections below are generated from `KPI_TABLES` / `KPI_LOG_LINES`;
regenerate them when those change.

## Inputs

Per device, per date, the day's logs rebuilt from its archives (`lib/log_archive.py`):
`<service>.log` (detailed logs) and `<service>_c.log` (critical-only subset), plus
`syslogs/log_<ts>.log` (syslog monitor). A service's files are matched by exact name, so
`inference` does not pick up `inference_inertial`.

Line formats (both read by `log_search.line_timestamp`):

```
<epoch_ms>: <service process uptime_ms>: <TAG>: <LVL>: <pid>: <tid>: <message>     C services
YYYY-MM-DD HH:MM:SS,mmm - <logger> - <LEVEL> - <message>                             Python services (UTC)
```

The second field is the uptime of that service process (it resets when the service
restarts), not the system uptime.

Most KPI lines are I-level, so they only exist in detailed logs. Devices that upload
critical logs only get the KPIs whose lines are marked "also in critical `_c` logs" in the
log line reference (camera crashes, VOD upload failures, AON version, cyclic reboot,
previous shutdown reason, ...).

## How a run computes them

1. **Day scan** (`kpi_metrics.scan_day`, run with every other scanner on the same read of the day):
   - One `grep -a -i -G -f <patterns>` pass per service log with the grep text of every KPI
     line of that service. Python then runs each line's regex only on the lines grep
     returned, to read its named groups (about 1.4% of a day's lines reach Python).
   - Lines with no timestamp (continuation lines) take the time of the line before them.
   - A line found in both `<service>.log` and `<service>_c.log` is kept once.
   - `awk` reads `ndcentral.log`, `power_mon.log`, `svc.log` and `conn_mgr.log` and keeps
     the time of the last line before every silence of 20 s or more, and of the file's last
     line ("alive" points, see below).
   - High volume lines are reduced: `frame_count` (every 5 s per camera) is summed per
     10 min and camera; `event_code` keeps the first line per uuid.
2. **Device** (`kpi_metrics.Device`, in `ota_report.finish_device`): the hits of all the
   device's days, its power-ons, and the report's boots.
3. **Per boot** (`kpi_metrics.compute`): every metric produces samples `(time, value)` over
   the whole device timeline; each sample goes to the report boot its time falls in.
   The boot's value of each metric (`boot_values`) is stored in the device JSON (`boots[].kpi`).
4. **Per device and OTA** (`kpi_metrics.summarize`): the samples of the device's boots on
   that OTA, combined by the metric's aggregation (see "Device value").
5. **OTA report** (`kpi_metrics.merge_summaries`, `ota_report._kpi_sections`): one table
   per KPI table, a row per device that has a value for any of its KPIs, then a Total row
   and a P90 row.

## Common rules

### Power-on (t = 0 of the "since power on" KPIs)

No line logs the moment of power on (or the boot LEDs), so it is derived from lines that
print the system uptime: `power-on = line epoch - system uptime`.

| Source (preference order) | Line | Logged at |
|---|---|---|
| `syslog_uptime` | syslog_monitor `uptime N.N s < 60 s` | 12-27 s, 0.1 s resolution |
| `ndc_system_uptime` | ndcentral `System Uptime: N seconds` | ndcentral start (~23 s bagheera3, ~27 s krait2), whole seconds |
| `pwr_uptime` | power_mon `check_uptime max_uptime M uptime_secs N`, N < 60 only | every 30 s |

All the estimates within 5 s of each other (`POWER_ON_CLUSTER_MS`) are one power-on, taken
from the most preferred source in the group. These power-ons split the device timeline into
segments; the "since power on" KPIs are measured per segment:
`value = epoch of the first matching line after the power-on - power-on`.
Values over 900 s (`MAX_SINCE_POWER_ON_S`) are dropped as another event, not the boot's.

Why not the report's own boot (a cluster of service restarts): it starts at the first
service start, ~21 s (bagheera3) to ~27 s (krait2) after power on, so lines logged before
that (`@Reboot Entering` at 12 s, the syslog uptime line) would land in the previous boot;
and a boot that hung after 25 s can be merged with the next one by the boot detection.

Devices with critical logs only have no uptime lines: their KPIs are taken per report boot
and the since-power-on KPIs are empty.

### Placing values in report boots

Each sample goes to the report boot its time falls in. A report boot starts at its
power-on when one lies at most 120 s (`POWER_ON_LEAD_MS`) before its first service start,
else at the first service start. Values whose time is before the run's start date or in the
day after the end date are dropped with their boots (as for every other part of the report).
Downtime KPIs start before a power-on and end after it: the value goes to the boot of the end line.

### Start -> end durations

`value = epoch(end line) - epoch(start line)`, the end being the next end line at or
after the start (with the same value of the pair group, when the KPI names one: session,
vod_id, thread id, camera). When several start lines come before one end line:

- start = first: a streak of failures is one retry period, from its first failure (retry durations).
- start = last: a repeated check, measured from the one just before the end (crank high soak, shutdown).

`same power-on` means the pair is dropped when a power-on lies between them.

### Last line before a reboot / power cut ("alive" points)

The last line of the boot that ended is the latest alive point before the new power-on.
Used by the abrupt power cycle, Tegra watchdog and LTE outage during OTA KPIs.

### Device value, Total row, P90 row

| Aggregation | Device value | Total row |
|---|---|---|
| sum over boots | sum of the samples | sum over the devices |
| mean of all samples | mean of every sample of the device (not a mean of boot means) | — |
| latest value | the last sample in time | — (text columns: P90 row shows the most common value) |
| % of boots | 100 x mean of the 0/1 samples | 100 x all boots with it / all boots (boot weighted) |

`P90` = 90th percentile of the device values in the column (the values shown in the table),
linear interpolation like numpy's default: sort the n values, `k = (n - 1) x 0.9`,
`P90 = v[floor(k)] + (v[floor(k) + 1] - v[floor(k)]) x (k - floor(k))`. The drive minutes
column gets one too.

### 0 versus empty

Count KPIs are 0 (not empty) in the boots whose logs could show the line: every boot for
lines that are also in critical logs, otherwise the boots with detailed logs (a boot with
an alive point, `ndc_service_uptime`, `syslog_uptime`, `pwr_uptime` or `record_start` line).
Other KPIs are empty when no line was found.

### Camera numbers

| Camera | Name | Product lines |
|---|---|---|
| 0 | Outward | all |
| 1 | Inward | all |
| 2 | Left | bagheera (`Bagheera3 Left cam 2: ...` in cam_rec) |
| 3 | Right | bagheera |
| 4, 5, 6, 7 | MDVR 4-7 | Octo: 4, 5, 6; krait2 MDVR: 4, 5 (placeholder files, video pulled on demand) |
| 8 | DMS | devices with a DMS camera |

The log lines are the same for every camera, only the number changes (`Pushed file 4_trip...`,
`CAM8: ... Frame count received`, `cam_num 6 is crashed`, `RECORD_START for cam num 8`,
`video: .../5_trip...`), so every per-camera KPI is made for each camera of `CAMS`.
Cameras 4-8 (`OPTIONAL_CAMS`) are computed only for devices that have the camera (a
`record_start`, `pushed_file`, `frame_count`, `cam_crash`, `vod_req` or `video_ok` line
names it), and their columns are shown only when some device on the OTA has a value; cameras
0-3 are always shown. storage_info lists cameras 4-7 together as `"N"` on the builds seen,
so their eMMC file counts appear only on builds that list them one by one.

### Constants (`lib/kpi_metrics.py`)

| Name | Value | Use |
|---|---|---|
| `POWER_ON_CLUSTER_MS` | 5 000 | power-on estimates this close are one power-on |
| `POWER_ON_LEAD_MS` | 120 000 | a report boot starts at a power-on this close before it |
| `MAX_SINCE_POWER_ON_S` | 900 | longer "since power on" values are dropped |
| `MAX_AFTER_IGNITION_S` | 300 | longer ignition -> BT advertising / scan values are dropped |
| `ALIVE_GAP_MS` | 20 000 | silence that makes an alive point |
| `MIN_FILE_BYTES` | 10 000 | smaller video / obs files (placeholders) are left out of sizes |
| `WAF_CYCLE_MS` | 5 000 | WAF lines this close are one diagnostic cycle |

## KPIs with more than one line

**Alert detection time (Alert Upload Time table).** The session key is the video name
without its leading camera digit (`trip..._<recording start epoch ms>_y`). Per session:

- recording start = the 13-digit epoch in the session name;
- detection = the earliest of the real-time `lla_msg_send` (outward / inward / DMS /
  inertial analytics client), the inertial `ialerts_found` with size > 31 bytes (31 = empty
  list) and inference `upload_state`;
- vision detection = earliest of `lla_msg_send` and `upload_state`;
- eventdata call = first `eventdata_call` whose session is the last `eventdata_summary`
  line on the same thread before it (the call line has no session);
- then the first `eventdata_ok`, `vod_req` and `video_ok` (per camera) of the session.

Rows 1-3 are measured from the recording start, the rest from the detection. Total = the
last camera's `video_ok` - detection. Values before the detection are dropped.

**LLA table.** One LLA per alert uuid of `lla_msg_send` (detection = its time, alert end =
its `end_timestamp`). NDCentral creation = next `ndc_lla_message` within 30 s (its line is
cut at ~300 chars, so it is paired by time, not uuid). Hand-off = `uploadadd` of
`lla_<uuid>_0`. Attempts = `lla_hs_result` lines of the uuid: API trigger = `uploadstart`
of attempt 0, success = `uploadend` of the passing attempt, retries = highest attempt
number, retry duration = passing (or last) `uploadend` - attempt 0 `uploadend`. Total =
passing `uploadend` - alert end.

**Obs Data Zip Upload table.** A batch = an `obs_req` line and the `obs_zip_start`,
`obs_zip_done`, `obs_call`, `obs_uploaded` lines after it on the same thread, up to the next
`obs_req` on that thread. Request received = `obs_req` time - its request time `rt`
(microseconds in the line; only the first batch of an `rt`, which repeats for the next
batches and retries). The other steps are measured from the `obs_req` line.

**Abrupt power cycles.** The power-on is classified by its service_mon
`Previous Shutdown:` reason: with `SUPERCAP_DISCHARGE` = with supercap; starting with
`POWER_DOWN` or `:SHUTDOWN_STATE:POWER_DOWN` (and not `SOFT_REBOOT`) = without. Downtime =
first frame after the power-on - last alive point before it. Reboot time = first frame -
power-on.

**Tegra watchdog.** A power-on whose wakeup / reset / previous shutdown reason contains
`WATCHDOG` (`RESET_REASON::POWER_DOWN:TEGRA_BCCPLEX_WATCHDOG`). Reboot Initiated = number
of them; Time taken to Reboot = its `pm_start` - last alive point before the power-on (the
hung boot's last line; the watchdog firing itself is not logged).

**LTE outage during OTA.** For each `updater_start`: the next `data_connected` within 30 min
(and before the next OTA attempt starts). Outage start = latest of the `conn_session_lost` /
`conn_exit` lines and the last conn_mgr alive point after the OTA start (the update kills
conn_mgr).

**Cyclic reboot.** Uptime = `uptime_secs` of the last `uptime_long` line within 60 s before
`soak_done`, only when a `shutdown_reason` restart for `SHUTDOWN_FOR_CYCLIC_REBOOT` follows
within 2 min (on some krait2 builds the reboot is cancelled and the device keeps running).

**GPS TTFF.** The value in `GPS First Fix time: N sec` (system uptime at the fix, so it
includes boot and service start). Low power wakeup = power-ons whose previous shutdown
reason contains `LOWPOWER`; cyclic / crank = reason contains `CYCLIC`, or wakeup reason
contains `IGNITION` / `CRANK`.

**Storage (storage_info).** deviceHealthClient `storage_info` buckets are `"count/MiB"` per
camera (`no_of_hq`, `no_of_lq`) and `no_of_obs`. File count = count (outward / inward:
hq + lq); average size = MiB x 1.048576 / count (MB); storage hours = outward lq count / 60
(one file a minute, as test TC_circbuff_777).

**/root size.** svc `Free space` bytes + diagnostic `used memory of /dev/mmcblk0` from the
nearest used line within 15 min: MiB on most builds (`free + used x 2^20`), a percentage on
some (`free / (1 - used / 100)`).

**WAF.** diagnostic `Waf is X` lines less than 5 s apart are one cycle: 1st = internal eMMC,
2nd = external eMMC (bagheera only).

**Bands Enabled.** Sierra band preference masks, hex: bit n of `LTE band pref from device is`
= band n+1, bit n of `bits_65_128` = band n+65. Quectel modems log no mask.

**Video file sizes.** circ_buff `Pushed file` lines: the last size per file name (a file is
re-pushed as it grows / on resync), files under 10 KB left out (privacy-mode and MDVR
placeholders). HD = `.mp4`, LD = `.mp4.ld.mp4`, obs = `.zip` (valid GPS: name without the
no-fix position `91.0000_181.0000`). FPS = frames summed per 10 min / (lines x 5 s).

## Metrics by table

### Alerts Summary

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| Total Alerts Detected | count | sum over boots | sum of N over "alerts.json contains N alert(s)" (final alert list of each session) | `alerts_contains` (inference) |
| Total Alerts Event Cards published | count | sum over boots | distinct sessions with "Upload successful for &lt;session&gt;.mp4" (event data upload) | `eventdata_ok` (unifieduploader) |
| Total Alerts Video uploaded - Cam 0 | count | sum over boots | distinct videos of cam 0 (Outward) with "Upload successful for video: .../0_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Total Alerts Video uploaded - Cam 1 | count | sum over boots | distinct videos of cam 1 (Inward) with "Upload successful for video: .../1_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Total Alerts Video uploaded - Cam 2 | count | sum over boots | distinct videos of cam 2 (Left) with "Upload successful for video: .../2_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Total Alerts Video uploaded - Cam 3 | count | sum over boots | distinct videos of cam 3 (Right) with "Upload successful for video: .../3_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Total Alerts Video uploaded - Cam 4 (MDVR 4) | count | sum over boots | distinct videos of cam 4 (MDVR 4) with "Upload successful for video: .../4_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Total Alerts Video uploaded - Cam 5 (MDVR 5) | count | sum over boots | distinct videos of cam 5 (MDVR 5) with "Upload successful for video: .../5_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Total Alerts Video uploaded - Cam 6 (MDVR 6) | count | sum over boots | distinct videos of cam 6 (MDVR 6) with "Upload successful for video: .../6_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Total Alerts Video uploaded - Cam 7 (MDVR 7) | count | sum over boots | distinct videos of cam 7 (MDVR 7) with "Upload successful for video: .../7_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Total Alerts Video uploaded - Cam 8 (DMS) | count | sum over boots | distinct videos of cam 8 (DMS) with "Upload successful for video: .../8_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Total Unique | count | sum over boots | distinct alert sessions moved to UPLOAD_STATE ("alerts found") | `upload_state` (inference) |

### Bootup, Video Recording & Connectivity

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| Device Bootup Time (Power ON) | s | mean of all samples | PARTIAL: power-on itself (left LED) is not logged; kernel start (anchor) to the first service start of the boot | `syslog_uptime` (syslogs)<br>`ndc_system_uptime` (ndcentral)<br>`pwr_uptime` (power_mon) |
| Device Bootup Time (Reboot Log) | s | mean of all samples | power-on anchor to the first "@Reboot Entering" of the boot | `reboot_entering` (reboot) |
| System Uptime (NDCentral) | s | mean of all samples | value of ndcentral "System Uptime: N seconds" (system uptime when ndcentral starts) | `ndc_system_uptime` (ndcentral) |
| Service Uptime (NDCentral) | s | mean of all samples | first ndcentral "serviceUpTime: N" of the boot (printed at the first video session) | `ndc_service_uptime` (ndcentral) |
| Modem Enumeration | s | mean of all samples | system uptime at "GPS Port Enumerate Time" (modem tty enumerated) | `gps_enum` (gps)<br>`sm_gps_enum` (service_mon) |
| SVC Service Start time | s | mean of all samples | power-on anchor to "######Starting SVC######" | `svc_start` (svc) |
| Analytics Service Start time | s | mean of all samples | power-on anchor to "######## Starting Analytics Service" | `analytics_start` (analytics) |
| ZMQ Socket Creation | s | mean of all samples | power-on anchor to cam_rec "SUCCESS IN CREATE zmq_socket" (bagheera; krait has no cam_rec) | `zmq_socket` (cam_rec) |
| First Frame Capture from Device Power ON | s | mean of all samples | power-on anchor to "first frame for cam num 0 registered@" | `first_frame` (ndcentral) |
| HW Button Detection - LED Glow for User Generated Alert | s | mean of all samples | PARTIAL: power-on anchor to svc "Button event thread created successfully" (button ready, incl. service uptime) | `button_ready` (svc) |
| Connection Manager - Data Connectivity time | s | mean of all samples | power-on anchor to "LTE Data Session started successfully" | `lte_session` (conn_mgr) |
| Keep Alive @ Reboot | s | mean of all samples | power-on anchor to the first power_mon keep alive "reached_cloud 1" | `ka_reached` (power_mon) |
| GPS TTFF - First Boot after Low Power Wakeup | s | mean of all samples | "GPS First Fix time: N sec" (system uptime at fix) in boots whose previous_shutdown_reason is a LOWPOWER one | `gps_ttff` (gps)<br>`prev_shutdown` (power_mon) |
| GPS TTFF - Cyclic Reboot / Crank Change | s | mean of all samples | same, in boots after a CYCLIC reboot or woken by IGNITION / crank | `gps_ttff` (gps)<br>`prev_shutdown` (power_mon)<br>`wakeup_reason` (power_mon/apm) |
| Cyclic Reboot Time (max_uptime) | h | mean of all samples | uptime_secs of the last "check_uptime" before "CYCLIC_REBOOT: Soak done Ns, rebooting", when the reboot followed (SHUTDOWN_FOR_CYCLIC_REBOOT) | `uptime_long` (power_mon)<br>`soak_done` (power_mon)<br>`shutdown_reason` (power_mon) |
| Cyclic Reboot for Crank High Detection | s | mean of all samples | last "CYCLIC_REBOOT: Uptime Check Pass HIGH" to "Soak done ..., rebooting" (the soak) | `pass_high` (power_mon)<br>`soak_done` (power_mon) |
| Device Shutdown Time | s | mean of all samples | "Device Shutdown Initiated" to "system_execute cmd: shutdown now\|poweroff\|reboot" (the configured delay before it, e.g. 720 s after ignition off, is not included) | `shutdown_initiated` (power_mon)<br>`shutdown_exec` (power_mon) |

### OTA & Firmware Details

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| OTA Package Size | MB | latest value | "OTA package size in bytes = N" (otacheck / updater) | `ota_size` (otacheck/updater) |
| OTA Update time | s | latest value | updateEngine "updateTime: N" (install only, not the download) | `sm_update_time` (service_mon)<br>`updater_update_time` (updater) |
| AON Firmware Version | text | latest value | "AON Ver: 0x.." (service_mon 41015) or apm "get_aon_application_version returns"; bagheera only | `aon_sm` (service_mon)<br>`aon_apm` (apm) |
| ISP Firmware Version | text | latest value | NOT_FOUND: no ISP firmware version in any uploaded log | — |
| OBD Firmware | text | latest value | obd "adapter fw ver : X" | `obd_fw` (obd) |
| Bands Enabled | text | latest value | PARTIAL: Sierra modems; LTE band preference masks decoded to band numbers (Quectel logs no mask) | `band_mask` (conn_mgr)<br>`band_ext` (conn_mgr) |

### Device Downtime / Data Loss

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| During OTA Update | s | mean of all samples | updater "-c stop -n bagheera" to the next "first frame for cam num 0" | `updater_stop` (updater)<br>`first_frame` (ndcentral) |
| Device Cyclic Reboot | s | mean of all samples | "Reboot - Cyclic" (service_mon 40016) to the next boot's first frame | `sm_cyclic` (service_mon)<br>`first_frame` (ndcentral) |
| Device Reboot from AWS IoT / Cloud Command | s | mean of all samples | awsiot "Received command: reboot-phone" to the next first frame | `iot_reboot` (awsiot)<br>`first_frame` (ndcentral) |
| Service Restart due to Camera Crash | s | mean of all samples | "cam_num N is crashed" to the next "RECORD_START for cam num N" | `cam_crash` (ndcentral)<br>`record_start` (ndcentral) |
| Device Abrupt Power Cycle (Without Supercap) | s | mean of all samples | boot whose "Previous Shutdown:" is a bare POWER_DOWN: last ndcentral/power_mon/svc line before it to its first frame | `sm_prev_shutdown` (service_mon)<br>`first_frame` (ndcentral) |
| Device Abrupt Power Cycle (With Supercap) | s | mean of all samples | same, for a "Previous Shutdown:" with SUPERCAP_DISCHARGE | `sm_prev_shutdown` (service_mon)<br>`first_frame` (ndcentral) |
| Device Reboot time after Abrupt Power Cycle (till video recording start) | s | mean of all samples | power-on anchor to the first frame, in the boots after an abrupt power cycle | `sm_prev_shutdown` (service_mon)<br>`first_frame` (ndcentral) |
| Connection Manager - LTE Outage during OTA Update | s | mean of all samples | PARTIAL: after "::Starting OTA update::", the last conn_mgr line / DISCONNECTED before the next "Success : Data session connected" to it | `updater_start` (updater)<br>`conn_session_lost` (conn_mgr)<br>`conn_exit` (conn_mgr)<br>`data_connected` (conn_mgr) |
| Connection Manager - LTE Outage during Device Disconnection | s | mean of all samples | PARTIAL: "Device disconnected : Exiting App" to the next "Success : Data session connected" without a reboot in between (at shutdown it is the device being off) | `conn_exit` (conn_mgr)<br>`data_connected` (conn_mgr) |

### Camera Crash Stats

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| Outward Camera Crash Count - Total | count | sum over boots | "cam_num 0 is crashed" (one per second) | `cam_crash` (ndcentral) |
| Outward Camera Crash Count % | % | % of boots | % of boots with drive time that had a cam 0 crash | `cam_crash` (ndcentral) |
| Inward Camera Crash Count - Total | count | sum over boots | "cam_num 1 is crashed" (one per second) | `cam_crash` (ndcentral) |
| Inward Camera Crash Count % | % | % of boots | % of boots with drive time that had a cam 1 crash | `cam_crash` (ndcentral) |
| Left Camera Crash Count - Total | count | sum over boots | "cam_num 2 is crashed" (one per second) | `cam_crash` (ndcentral) |
| Left Camera Crash Count % | % | % of boots | % of boots with drive time that had a cam 2 crash | `cam_crash` (ndcentral) |
| Right Camera Crash Count - Total | count | sum over boots | "cam_num 3 is crashed" (one per second) | `cam_crash` (ndcentral) |
| Right Camera Crash Count % | % | % of boots | % of boots with drive time that had a cam 3 crash | `cam_crash` (ndcentral) |
| MDVR 4 Camera Crash Count - Total | count | sum over boots | "cam_num 4 is crashed" (one per second) | `cam_crash` (ndcentral) |
| MDVR 4 Camera Crash Count % | % | % of boots | % of boots with drive time that had a cam 4 crash | `cam_crash` (ndcentral) |
| MDVR 5 Camera Crash Count - Total | count | sum over boots | "cam_num 5 is crashed" (one per second) | `cam_crash` (ndcentral) |
| MDVR 5 Camera Crash Count % | % | % of boots | % of boots with drive time that had a cam 5 crash | `cam_crash` (ndcentral) |
| MDVR 6 Camera Crash Count - Total | count | sum over boots | "cam_num 6 is crashed" (one per second) | `cam_crash` (ndcentral) |
| MDVR 6 Camera Crash Count % | % | % of boots | % of boots with drive time that had a cam 6 crash | `cam_crash` (ndcentral) |
| MDVR 7 Camera Crash Count - Total | count | sum over boots | "cam_num 7 is crashed" (one per second) | `cam_crash` (ndcentral) |
| MDVR 7 Camera Crash Count % | % | % of boots | % of boots with drive time that had a cam 7 crash | `cam_crash` (ndcentral) |
| DMS Camera Crash Count - Total | count | sum over boots | "cam_num 8 is crashed" (one per second) | `cam_crash` (ndcentral) |
| DMS Camera Crash Count % | % | % of boots | % of boots with drive time that had a cam 8 crash | `cam_crash` (ndcentral) |

### Video File Size Details - HD

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| Outward Video - File Size - HD | MB | mean of all samples | circ_buff "Pushed file 0_&lt;session&gt;.mp4 with size N" (last per file, files &lt; 10 KB dropped) | `pushed_file` (circ_buff) |
| Inward Video - File Size - HD | MB | mean of all samples | circ_buff "Pushed file 1_&lt;session&gt;.mp4 with size N" (last per file, files &lt; 10 KB dropped) | `pushed_file` (circ_buff) |
| Left Video - File Size - HD | MB | mean of all samples | circ_buff "Pushed file 2_&lt;session&gt;.mp4 with size N" (last per file, files &lt; 10 KB dropped) | `pushed_file` (circ_buff) |
| Right Video - File Size - HD | MB | mean of all samples | circ_buff "Pushed file 3_&lt;session&gt;.mp4 with size N" (last per file, files &lt; 10 KB dropped) | `pushed_file` (circ_buff) |
| MDVR 4 Video - File Size - HD | MB | mean of all samples | circ_buff "Pushed file 4_&lt;session&gt;.mp4 with size N" (last per file, files &lt; 10 KB dropped) | `pushed_file` (circ_buff) |
| MDVR 5 Video - File Size - HD | MB | mean of all samples | circ_buff "Pushed file 5_&lt;session&gt;.mp4 with size N" (last per file, files &lt; 10 KB dropped) | `pushed_file` (circ_buff) |
| MDVR 6 Video - File Size - HD | MB | mean of all samples | circ_buff "Pushed file 6_&lt;session&gt;.mp4 with size N" (last per file, files &lt; 10 KB dropped) | `pushed_file` (circ_buff) |
| MDVR 7 Video - File Size - HD | MB | mean of all samples | circ_buff "Pushed file 7_&lt;session&gt;.mp4 with size N" (last per file, files &lt; 10 KB dropped) | `pushed_file` (circ_buff) |
| DMS Video - File Size - HD | MB | mean of all samples | circ_buff "Pushed file 8_&lt;session&gt;.mp4 with size N" (last per file, files &lt; 10 KB dropped) | `pushed_file` (circ_buff) |
| Outward Video - FPS - HD | fps | mean of all samples | "CAM0: Frame count received: N" every 5 s: frames / time | `frame_count` (ndcentral/cam_rec) |
| Inward Video - FPS - HD | fps | mean of all samples | "CAM1: Frame count received: N" every 5 s: frames / time | `frame_count` (ndcentral/cam_rec) |
| Left Video - FPS - HD | fps | mean of all samples | "CAM2: Frame count received: N" every 5 s: frames / time | `frame_count` (ndcentral/cam_rec) |
| Right Video - FPS - HD | fps | mean of all samples | "CAM3: Frame count received: N" every 5 s: frames / time | `frame_count` (ndcentral/cam_rec) |
| MDVR 4 Video - FPS - HD | fps | mean of all samples | "CAM4: Frame count received: N" every 5 s: frames / time | `frame_count` (ndcentral/cam_rec) |
| MDVR 5 Video - FPS - HD | fps | mean of all samples | "CAM5: Frame count received: N" every 5 s: frames / time | `frame_count` (ndcentral/cam_rec) |
| MDVR 6 Video - FPS - HD | fps | mean of all samples | "CAM6: Frame count received: N" every 5 s: frames / time | `frame_count` (ndcentral/cam_rec) |
| MDVR 7 Video - FPS - HD | fps | mean of all samples | "CAM7: Frame count received: N" every 5 s: frames / time | `frame_count` (ndcentral/cam_rec) |
| DMS Video - FPS - HD | fps | mean of all samples | "CAM8: Frame count received: N" every 5 s: frames / time | `frame_count` (ndcentral/cam_rec) |

### Video File Size Details - LD

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| Outward Video - File Size - LD | MB | mean of all samples | "Pushed file 0_&lt;session&gt;.mp4.ld.mp4 with size N" | `pushed_file` (circ_buff) |
| Inward Video - File Size - LD | MB | mean of all samples | "Pushed file 1_&lt;session&gt;.mp4.ld.mp4 with size N" | `pushed_file` (circ_buff) |
| Left Video - File Size - LD | MB | mean of all samples | NOT_FOUND: left camera writes one (already LD) file a minute, see the HD size | — |
| Right Video - File Size - LD | MB | mean of all samples | NOT_FOUND: right camera writes one (already LD) file a minute, see the HD size | — |
| MDVR 4 Video - File Size - LD | MB | mean of all samples | "Pushed file 4_&lt;session&gt;.mp4.ld.mp4 with size N" | `pushed_file` (circ_buff) |
| MDVR 5 Video - File Size - LD | MB | mean of all samples | "Pushed file 5_&lt;session&gt;.mp4.ld.mp4 with size N" | `pushed_file` (circ_buff) |
| MDVR 6 Video - File Size - LD | MB | mean of all samples | "Pushed file 6_&lt;session&gt;.mp4.ld.mp4 with size N" | `pushed_file` (circ_buff) |
| MDVR 7 Video - File Size - LD | MB | mean of all samples | "Pushed file 7_&lt;session&gt;.mp4.ld.mp4 with size N" | `pushed_file` (circ_buff) |
| DMS Video - File Size - LD | MB | mean of all samples | "Pushed file 8_&lt;session&gt;.mp4.ld.mp4 with size N" | `pushed_file` (circ_buff) |
| Outward Video - FPS - LD | fps | mean of all samples | PARTIAL: LD is encoded from the same frames, so the HD frame count | `frame_count` (ndcentral/cam_rec) |
| Inward Video - FPS - LD | fps | mean of all samples | PARTIAL: LD is encoded from the same frames, so the HD frame count | `frame_count` (ndcentral/cam_rec) |

### Preview LQ Videos - Video File Size Details

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| Outward - LD File Size (Average) | MB | latest value | PARTIAL: storage_info no_of_lq["0"] MiB / file count | `storage_info` (deviceHealthClient) |
| Inward - LD File Size (Average) | MB | latest value | PARTIAL: storage_info no_of_lq["1"] MiB / file count | `storage_info` (deviceHealthClient) |
| Outward - HD File Size (Average) | MB | latest value | PARTIAL: storage_info no_of_hq["0"] MiB / file count | `storage_info` (deviceHealthClient) |
| Inward - HD File Size (Average) | MB | latest value | PARTIAL: storage_info no_of_hq["1"] MiB / file count | `storage_info` (deviceHealthClient) |

### Obs File Size Details

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| Size Allocated for Obs Files (/home/ubuntu/.nddevice/observations) | MB | latest value | NOT_FOUND: no quota is logged | — |
| Max HD Obs files can be stored - File Count | count | latest value | PARTIAL: obs files on the card (storage_info no_of_obs), the steady state once the card is full | `storage_info` (deviceHealthClient) |
| HD Obs - Avg size of file | KB | mean of all samples | circ_buff "Pushed file 0_&lt;session&gt;.zip with size N" (last per file) | `pushed_file` (circ_buff) |
| Max LD Obs files can be stored - File Count | count | latest value | NOT_FOUND: needs the obs allocation, which is not logged | — |
| LD Obs - Avg size of file | KB | mean of all samples | inference "obsZipDestFile size: b'.../observations/...zip' -- N" | `obs_ld_size` (inference) |
| Obs File Size (One zip with valid GPS) | KB | mean of all samples | HD obs zips whose name has a GPS fix (not 91.0000_181.0000) | `pushed_file` (circ_buff) |

### SdCard / eMMC

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| Total Outward files in eMMC | count | latest value | storage_info no_of_hq["0"] + no_of_lq["0"] file counts | `storage_info` (deviceHealthClient) |
| Total Inward files in eMMC | count | latest value | storage_info no_of_hq["1"] + no_of_lq["1"] | `storage_info` (deviceHealthClient) |
| Total Left files in eMMC | count | latest value | storage_info no_of_hq["2"] | `storage_info` (deviceHealthClient) |
| Total Right files in eMMC | count | latest value | storage_info no_of_hq["3"] | `storage_info` (deviceHealthClient) |
| Total MDVR 4 files in eMMC | count | latest value | storage_info no_of_hq["4"] + no_of_lq["4"] (builds that list the camera on its own) | `storage_info` (deviceHealthClient) |
| Total MDVR 5 files in eMMC | count | latest value | storage_info no_of_hq["5"] + no_of_lq["5"] (builds that list the camera on its own) | `storage_info` (deviceHealthClient) |
| Total MDVR 6 files in eMMC | count | latest value | storage_info no_of_hq["6"] + no_of_lq["6"] (builds that list the camera on its own) | `storage_info` (deviceHealthClient) |
| Total MDVR 7 files in eMMC | count | latest value | storage_info no_of_hq["7"] + no_of_lq["7"] (builds that list the camera on its own) | `storage_info` (deviceHealthClient) |
| Total DMS files in eMMC | count | latest value | storage_info no_of_hq["8"] + no_of_lq["8"] (builds that list the camera on its own) | `storage_info` (deviceHealthClient) |
| Obs files in eMMC | count | latest value | storage_info no_of_obs | `storage_info` (deviceHealthClient) |
| eMMC - Storage Hours | h | latest value | outward LD files / 60 (one file a minute), as test TC_circbuff_777 | `storage_info` (deviceHealthClient) |

### Internal Storage - /root

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| Storage Size | GB | latest value | PARTIAL: svc "DISK: Free space" + diagnostic "used memory of /dev/mmcblk0" (MiB, or % on some builds) | `disk_free` (svc)<br>`disk_used` (diagnostic) |

### WAF - eMMC Metrics

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| External eMMC | WAF | latest value | 2nd "Waf is X" of a diagnostic WAF cycle (bagheera only) | `waf` (diagnostic) |
| Internal eMMC | WAF | latest value | 1st "Waf is X" of a diagnostic WAF cycle | `waf` (diagnostic) |

### Tegra Watchdog Timeout

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| Reboot Initiated | count | sum over boots | boots reset by the watchdog (RESET_REASON / previous_shutdown_reason with WATCHDOG); the watchdog firing itself is not logged | `wakeup_reason` (power_mon/apm)<br>`prev_shutdown` (power_mon) |
| Power Monitor Start after reboot | s | mean of all samples | power-on anchor to "#####STARTING POWER MONITOR#####" in the watchdog boot | `pm_start` (power_mon) |
| Time taken to Reboot | s | mean of all samples | PARTIAL: last ndcentral/power_mon/svc line of the hung boot to the power monitor start | `pm_start` (power_mon) |

### Installer App - Metrics

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| Ignition ON Detected | s | mean of all samples | power-on anchor to btfv "IGNITION ON received" | `bt_ignition_on` (btfv) |
| Scan for Installer App Starts | s | mean of all samples | IGNITION ON to "Inside: RegisterInstallerAppFilter" | `bt_ignition_on` (btfv)<br>`installer_scan_start` (btfv) |
| Scan for Installer App Stops | s | mean of all samples | IGNITION ON to "InstallerApp timeout cb triggered" | `bt_ignition_on` (btfv)<br>`installer_scan_stop` (btfv) |

### Driveri App & BT Beacon - Metrics

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| Ignition ON Detected | s | mean of all samples | power-on anchor to btfv "IGNITION ON received" | `bt_ignition_on` (btfv) |
| Advertising Started | s | mean of all samples | IGNITION ON to "Starting Driver Login Advertisement" | `bt_ignition_on` (btfv)<br>`driver_adv_start` (btfv) |
| Scan for DriveriApp / BT Beacon Starts | s | mean of all samples | IGNITION ON to "Inside: RegisterDriverLoginLegacyFilter" | `bt_ignition_on` (btfv)<br>`driver_scan_start` (btfv) |
| Scan for DriveriApp / BT Beacon Stops | s | mean of all samples | IGNITION ON to the first "DriverLogin legacy scan complete cb triggered" | `bt_ignition_on` (btfv)<br>`driver_scan_stop` (btfv) |

### Alert Upload Time - Metrics

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| File Recording Start time | s | mean of all samples | recording start of the alert file (epoch in its name) to the alert detection | `upload_state` (inference)<br>`lla_msg_send` (outwardAnalyticsClient/inwardAnalyticsClient/dmsAnalyticsClient/inertialAnalyticsClient)<br>`ialerts_found` (inference_inertial) |
| Inertial Alert Detection | s | mean of all samples | PARTIAL: recording start to inference_inertial "ialerts.json found at .../&lt;session&gt;/" (non-empty) | `ialerts_found` (inference_inertial) |
| Inference / Vision Alert Detection / Processing | s | mean of all samples | recording start to the earliest of the analytics "msg_send" (real time) and inference UPLOAD_STATE | `lla_msg_send` (outwardAnalyticsClient/inwardAnalyticsClient/dmsAnalyticsClient/inertialAnalyticsClient)<br>`upload_state` (inference) |
| Alert Notification to the Cloud - API Call Triggering | s | mean of all samples | detection to the first /upload/eventdata call of the session (session: the "Summary file size" line before it on the same thread) | `eventdata_summary` (unifieduploader)<br>`eventdata_call` (unifieduploader) |
| Alert Notification to the Cloud - API Call Successful | s | mean of all samples | detection to "Upload successful for &lt;session&gt;.mp4" | `eventdata_ok` (unifieduploader) |
| Upload Video Request Received from Cloud - Outward | s | mean of all samples | detection to the first "VOD req received ... video: .../0_&lt;session&gt;.mp4" | `vod_req` (unifieduploader) |
| Upload Video Request Received from Cloud - Inward | s | mean of all samples | detection to the first "VOD req received ... video: .../1_&lt;session&gt;.mp4" | `vod_req` (unifieduploader) |
| Upload Video Request Received from Cloud - Left | s | mean of all samples | detection to the first "VOD req received ... video: .../2_&lt;session&gt;.mp4" | `vod_req` (unifieduploader) |
| Upload Video Request Received from Cloud - Right | s | mean of all samples | detection to the first "VOD req received ... video: .../3_&lt;session&gt;.mp4" | `vod_req` (unifieduploader) |
| Upload Video Request Received from Cloud - MDVR 4 | s | mean of all samples | detection to the first "VOD req received ... video: .../4_&lt;session&gt;.mp4" | `vod_req` (unifieduploader) |
| Upload Video Request Received from Cloud - MDVR 5 | s | mean of all samples | detection to the first "VOD req received ... video: .../5_&lt;session&gt;.mp4" | `vod_req` (unifieduploader) |
| Upload Video Request Received from Cloud - MDVR 6 | s | mean of all samples | detection to the first "VOD req received ... video: .../6_&lt;session&gt;.mp4" | `vod_req` (unifieduploader) |
| Upload Video Request Received from Cloud - MDVR 7 | s | mean of all samples | detection to the first "VOD req received ... video: .../7_&lt;session&gt;.mp4" | `vod_req` (unifieduploader) |
| Upload Video Request Received from Cloud - DMS | s | mean of all samples | detection to the first "VOD req received ... video: .../8_&lt;session&gt;.mp4" | `vod_req` (unifieduploader) |
| Upload Video Successful - Outward | s | mean of all samples | detection to "Upload successful for video: .../0_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Upload Video Successful - Inward | s | mean of all samples | detection to "Upload successful for video: .../1_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Upload Video Successful - Left | s | mean of all samples | detection to "Upload successful for video: .../2_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Upload Video Successful - Right | s | mean of all samples | detection to "Upload successful for video: .../3_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Upload Video Successful - MDVR 4 | s | mean of all samples | detection to "Upload successful for video: .../4_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Upload Video Successful - MDVR 5 | s | mean of all samples | detection to "Upload successful for video: .../5_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Upload Video Successful - MDVR 6 | s | mean of all samples | detection to "Upload successful for video: .../6_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Upload Video Successful - MDVR 7 | s | mean of all samples | detection to "Upload successful for video: .../7_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Upload Video Successful - DMS | s | mean of all samples | detection to "Upload successful for video: .../8_&lt;session&gt;.mp4" | `video_ok` (unifieduploader) |
| Total Time Taken for upload (from alert detection) | s | mean of all samples | detection to the last camera's video upload success of the session | `video_ok` (unifieduploader) |

### Obs Data Zip Upload - Metrics

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| Upload Request for Obs zip - Received | s | mean of all samples | request time (rt, in the line) to "Upload Observations Request Received" | `obs_req` (unifieduploader) |
| Files adding into zip - Completion (10 files) | s | mean of all samples | request received to "Creating observations zip file at once!" (last file added) | `obs_req` (unifieduploader)<br>`obs_zip_start` (unifieduploader) |
| Creating Obs Zip | s | mean of all samples | "Creating observations zip file at once!" to "Obs zip file created!" | `obs_zip_start` (unifieduploader)<br>`obs_zip_done` (unifieduploader) |
| Uploading Obs Zip - Start | s | mean of all samples | request received to "Calling service: .../upload/observations" | `obs_req` (unifieduploader)<br>`obs_call` (unifieduploader) |
| Uploading Obs Zip - Successful | s | mean of all samples | request received to "File uploaded. Deleting" | `obs_req` (unifieduploader)<br>`obs_uploaded` (unifieduploader) |

### Retry Count & Duration for API / Upload Calls

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| upload-eventdata - Retry Count | count | sum over boots | "Details: type - msg type: 100" (one per retry) | `retry_details` (unifieduploader) |
| upload-eventdata - Retry Duration | s | mean of all samples | first "Upload failed for &lt;session&gt;.mp4" to its "Upload successful" | `eventdata_fail` (unifieduploader)<br>`eventdata_ok` (unifieduploader) |
| upload-video - Retry Count | count | sum over boots | "VOD upload attempt failed" minus "Exhausted all upload retries" (critical logs too) | `vod_attempt_failed` (unifieduploader)<br>`vod_exhausted` (unifieduploader) |
| upload-video - Retry Duration | s | mean of all samples | first failed attempt of a vod_id to "deleted VOD upload req ... - &lt;vod_id&gt; from DB" (uploaded or given up) | `vod_attempt_failed` (unifieduploader)<br>`vod_deleted` (unifieduploader) |
| upload-observationdata - Retry Count | count | sum over boots | "Details: type - msg type: 110" | `retry_details` (unifieduploader) |
| upload-observationdata - Retry Duration | s | mean of all samples | first "Failed to fetch url (.../upload/observations)" to the next "File uploaded" on that thread | `obs_fail` (unifieduploader)<br>`obs_uploaded` (unifieduploader) |
| videolist - Retry Count | count | sum over boots | PARTIAL: "collect_notify_videolist is FAILED" (the next periodic call is the retry) | `videolist_failed` (circ_buff) |
| videolist - Retry Duration | s | mean of all samples | PARTIAL: first FAILED to the next "is succeded" | `videolist_failed` (circ_buff)<br>`videolist_ok` (circ_buff) |
| upload-device-status - Retry Count | count | sum over boots | health "curl_with_health: curl exited N" (failed status POST, re-sent from backup) | `health_post_failed` (health) |
| upload-device-status - Retry Duration | s | mean of all samples | first failed POST to the next "Upload of Health Stats successful" | `health_post_failed` (health)<br>`health_ok` (health) |

### LLA - Metrics

| KPI | Unit | Device value | How it is computed | Log lines used |
|---|---|---|---|---|
| File Recording Start time | s | mean of all samples | recording start of the alert file (epoch in videoName) to the analytics "msg_send" (LLA detection) | `lla_msg_send` (outwardAnalyticsClient/inwardAnalyticsClient/dmsAnalyticsClient/inertialAnalyticsClient) |
| NDCentral LLA creation | s | mean of all samples | msg_send to the next ndcentral "lla_message :{" | `lla_msg_send` (outwardAnalyticsClient/inwardAnalyticsClient/dmsAnalyticsClient/inertialAnalyticsClient)<br>`ndc_lla_message` (ndcentral) |
| NDCentral LLA notifying to uploader | s | mean of all samples | msg_send to the uploader "lla_&lt;uuid&gt;_0": {"uploadadd": T} (hand-off received) | `lla_msg_send` (outwardAnalyticsClient/inwardAnalyticsClient/dmsAnalyticsClient/inertialAnalyticsClient)<br>`lla_hs_add` (unifieduploader) |
| Uploader LLA Notification to the Cloud - API Call Triggering | s | mean of all samples | msg_send to "uploadstart" of attempt 0 | `lla_hs_result` (unifieduploader) |
| Uploader LLA Notification to the Cloud - API Call Successful | s | mean of all samples | msg_send to "uploadend" of the attempt with status pass | `lla_hs_result` (unifieduploader) |
| Total Time Taken for upload (from alert detection) | s | mean of all samples | alert end_timestamp (in msg_send) to the passing "uploadend" | `lla_msg_send` (outwardAnalyticsClient/inwardAnalyticsClient/dmsAnalyticsClient/inertialAnalyticsClient)<br>`lla_hs_result` (unifieduploader) |
| LLA retry count | count | sum over boots | highest attempt n of "lla_&lt;uuid&gt;_&lt;n&gt;" per LLA | `lla_hs_result` (unifieduploader) |
| LLA retry duration | s | mean of all samples | uploadend of attempt 0 to the passing (or last) attempt's uploadend, for LLAs that retried | `lla_hs_result` (unifieduploader) |
### Analytics KPIs

| KPI | How it is computed | Log lines used |
|---|---|---|
| Unique event codes and the count against each | each analytics event once (by uuid, over inference and inference_inertial), counted per event code; table: code, unique events, devices with it, P90 of the per-device counts | `event_code` (inference/inference_inertial) |

The `is_alert=` lines list every analytics event (turns, backing, ...), not only the ones
raised as alerts; alert counts are in the Alerts Summary table.

## Not found in the logs

These KPIs stay in the tables, empty:

- ISP Firmware Version: no ISP version in any uploaded log (krait's `isp_flash.log` is not uploaded).
- Left / Right Video - File Size - LD: left and right cameras write one (already LD) file a minute; see the HD size.
- Size Allocated for Obs Files, Max LD Obs files can be stored: no obs quota is logged.

Partial (a stand-in for what the KPI asks): Device Bootup Time (Power ON), HW Button
Detection, Bands Enabled, LTE outages, LD FPS, Preview LQ sizes, Max HD Obs files, /root
Storage Size, videolist retries, Tegra watchdog Time taken to Reboot. The table rows above
say what each one measures.

## Log line reference

Every line the KPIs read (`KPI_LOG_LINES`). grep text is what the grep pass matches
(case-insensitive); the regex then reads the named groups. Samples are verbatim lines from
the device-day in brackets (`<pid>` / `<tid>` mark lines quoted from the pattern only).

#### `alive`

- Service log: `ndcentral.log`, `power_mon.log`, `svc.log`, `conn_mgr.log` (detailed logs only)
- Pattern: awk over every line starting with a 13-digit epoch: prints the epoch of the last line before each gap of 20 s or more, and of the last line
- Used by: abrupt power cycles, Tegra watchdog, LTE outage during OTA, 0 versus empty

#### `syslog_uptime`

- Service log: `syslogs`
- grep text: `uptime `
- Regex: `SYSLOG_MON: I: \d+: \d+: uptime (?P<s>\d+\.\d+) s < 60 s`
- Sample: `1793148914428: 2061: SYSLOG_MON: I: 6030: 6030: uptime 26.4 s < 60 s: starting background scan from start of file (270335 bytes)  [103102600722_2026-10-28/syslogs/log_1793148913598.log]`
- Used by: power-on anchor, Device Bootup Time (Power ON)

#### `ndc_system_uptime`

- Service log: `ndcentral`
- grep text: `System Uptime: `
- Regex: `NDC: I: \d+: \d+: System Uptime: (?P<s>\d+) seconds`
- Sample: `1793148911737: 206: NDC: I: 6110: 6110: System Uptime: 23 seconds  [103102600722_2026-10-28/ndcentral.log]`
- Used by: power-on anchor, Device Bootup Time (Power ON), System Uptime (NDCentral)

#### `pwr_uptime`

- Service log: `power_mon`
- grep text: `check_uptime max_uptime`
- Regex: `check_uptime max_uptime \d+ uptime_secs (?P<s>[1-5]?\d)$`
- Sample: `1793143047604: 6940451: PWR: I: 5922: 6755: check_uptime max_uptime 172800 uptime_secs 6964  [103102600722_2026-10-28/power_mon.log] (only the first minute, < 60 s, is kept: anchor)`
- Used by: power-on anchor, Device Bootup Time (Power ON)

#### `reboot_entering`

- Service log: `reboot`
- grep text: `@Reboot Entering`
- Regex: `@Reboot Entering`
- Sample: `2026-10-28 00:55:02,578 - reboot - INFO - ::====================::@Reboot Entering::====================::  [103102600722_2026-10-28/reboot.log]`
- Used by: Device Bootup Time (Reboot Log)

#### `ndc_service_uptime`

- Service log: `ndcentral`
- grep text: `serviceUpTime: `
- Regex: `NDC: I: \d+: \d+: serviceUpTime: (?P<s>\d+)$`
- Sample: `1793148927548: 16017: NDC: I: 6110: 6110: serviceUpTime: 16  [103102600722_2026-10-28/ndcentral.log]`
- Used by: Service Uptime (NDCentral)

#### `gps_enum`

- Service log: `gps`
- grep text: `GPS Port Enumerate Time`
- Regex: `GPSD: I: \d+: \d+: GPS Port Enumerate Time: (?P<s>\d+) sec$`
- Sample: `1793148919561: 7538: GPSD: I: 5975: 6266: GPS Port Enumerate Time: 31 sec  [103102600722_2026-10-28/gps.log]`
- Used by: Modem Enumeration

#### `sm_gps_enum`

- Service log: `service_mon`  (also in critical `_c` logs)
- grep text: `GPS Port Enumerate Time`
- Regex: `ND_GPS : \d+ : \d+ : -?\d+ : GPS Port Enumerate Time: \d+ seconds : (?P<ms>\d+)`
- Sample: `Service error: ND_GPS : <epoch> : <code> : <n> : GPS Port Enumerate Time: <s> seconds : <ms>  [service_mon_c.log; critical-only fallback of gps_enum]`
- Used by: Modem Enumeration

#### `svc_start`

- Service log: `svc`
- grep text: `Starting SVC`
- Regex: `######Starting SVC######`
- Sample: `1793148910115: 331: SVC: I: 5924: 5924: ######Starting SVC######  [103102600722_2026-10-28/svc.log]`
- Used by: SVC Service Start time

#### `analytics_start`

- Service log: `analytics`
- grep text: `Starting Analytics Service`
- Regex: `######## Starting Analytics Service`
- Sample: `1793148910904: 200: AS: I: 5911: 5911: ######## Starting Analytics Service ######  [103102600722_2026-10-28/analytics.log]`
- Used by: Analytics Service Start time

#### `zmq_socket`

- Service log: `cam_rec`
- grep text: `SUCCESS IN CREATE zmq_socket`
- Regex: `CAM_REC: I: \d+: \d+: SUCCESS IN CREATE zmq_socket`
- Sample: `1793148925369: 7991: CAM_REC: I: 6440: 6440: SUCCESS IN CREATE zmq_socket  [103102600722_2026-10-28/cam_rec.log]`
- Used by: ZMQ Socket Creation

#### `first_frame`

- Service log: `ndcentral`
- grep text: `first frame for cam num 0`
- Regex: `first frame for cam num 0 registered@ (?P<epoch>\d{13})`
- Sample: `1793148927351: 15820: NDC: I: 6110: 6920: first frame for cam num 0 registered@ 1793148927351  [103102600722_2026-10-28/ndcentral.log]`
- Used by: First Frame Capture from Device Power ON, During OTA Update, Device Cyclic Reboot, Device Reboot from AWS IoT / Cloud Command, Device Abrupt Power Cycle (Without Supercap), Device Abrupt Power Cycle (With Supercap), Device Reboot time after Abrupt Power Cycle (till video recording start)

#### `button_ready`

- Service log: `svc`
- grep text: `Button event thread created successfully`
- Regex: `Button event thread created successfully`
- Sample: `1793684837920: 1156: SVC: I: 3673: 3673: Button event thread created successfully  [264080392_2026-11-03/svc.log]`
- Used by: HW Button Detection - LED Glow for User Generated Alert

#### `lte_session`

- Service log: `conn_mgr`
- grep text: `LTE Data Session started successfully`
- Regex: `CONN_MGR_SESSION: I: \d+: \d+: LTE Data Session started successfully`
- Sample: `1793148969342: 6563: CONN_MGR_SESSION: I: 8001: 8001: LTE Data Session started successfully  [103102600722_2026-10-28/conn_mgr.log]`
- Used by: Connection Manager - Data Connectivity time

#### `ka_reached`

- Service log: `power_mon`
- grep text: `reached_cloud 1`
- Regex: `response_string :.*: reached_cloud 1`
- Sample: `1793148973182: 61191: PWR: I: 6010: 6802: response_string :{"response":true,"msg":"upload-logs"}: reached_cloud 1  [103102600722_2026-10-28/power_mon.log]`
- Used by: Keep Alive @ Reboot

#### `gps_ttff`

- Service log: `gps`
- grep text: `GPS First Fix time`
- Regex: `GPS First Fix time: (?P<s>\d+) sec`
- Sample: `1793155724563: 15871: GPSD: I: 5879: 6019: GPS First Fix time: 37 sec  [103102600722_2026-10-28/gps.log]`
- Used by: GPS TTFF - First Boot after Low Power Wakeup, GPS TTFF - Cyclic Reboot / Crank Change

#### `prev_shutdown`

- Service log: `power_mon`
- grep text: `previous_shutdown_reason`
- Regex: `POWER_MONITOR_ctx->previous_shutdown_reason (?P<reason>\S+)`
- Sample: `1793155718957: 9032: PWR: I: 5880: 5880: POWER_MONITOR_ctx->previous_shutdown_reason DBSTATE_SHUTDOWN_MISC_LOWPOWER:SHUTDOWN  [103102600722_2026-10-28/power_mon.log]`
- Used by: GPS TTFF - First Boot after Low Power Wakeup, GPS TTFF - Cyclic Reboot / Crank Change, Reboot Initiated

#### `wakeup_reason`

- Service log: `power_mon/apm`
- grep text: `WAKEUP_REASON:`
- Regex: `DEV\w*: I: \d+: \d+: WAKEUP_REASON:\s*(?P<wakeup>.+?)\s*--\s*RESET_REASON:\s*(?P<reset>\S+)`
- Sample: `1793239013133: 10281: DEVB3: I: 5973: 5973: WAKEUP_REASON::POWER_UP -- RESET_REASON::POWER_DOWN:TEGRA_BCCPLEX_WATCHDOG  [log_prod/103102600722/2026-10-29/power_mon]`
- Used by: GPS TTFF - Cyclic Reboot / Crank Change, Reboot Initiated

#### `pass_high`

- Service log: `power_mon`  (also in critical `_c` logs)
- grep text: `Uptime Check Pass HIGH`
- Regex: `CYCLIC_REBOOT: Uptime Check Pass HIGH`
- Sample: `1793566422289: 172763949: PWR: C: 3757: 4125: CYCLIC_REBOOT: Uptime Check Pass HIGH  [log_prod/6603078588/2026-11-01/power_mon]`
- Used by: Cyclic Reboot for Crank High Detection

#### `uptime_long`

- Service log: `power_mon`
- grep text: `check_uptime max_uptime`
- Regex: `check_uptime max_uptime (?P<max>\d+) uptime_secs (?P<s>[1-9]\d{5,})$`
- Sample: `1793568222631: 174564291: PWR: I: 3757: 4125: check_uptime max_uptime 172800 uptime_secs 174609  [log_prod/6603078588/2026-11-01/power_mon] (only uptimes >= 100000 s are kept)`
- Used by: Cyclic Reboot Time (max_uptime)

#### `soak_done`

- Service log: `power_mon`  (also in critical `_c` logs)
- grep text: `CYCLIC_REBOOT: Soak done`
- Regex: `CYCLIC_REBOOT: Soak done (?P<soak>\d+)s, rebooting`
- Sample: `1793568222635: 174564295: PWR: C: 3757: 4125: CYCLIC_REBOOT: Soak done 1800s, rebooting  [log_prod/6603078588/2026-11-01/power_mon]`
- Used by: Cyclic Reboot Time (max_uptime), Cyclic Reboot for Crank High Detection

#### `shutdown_reason`

- Service log: `power_mon`  (also in critical `_c` logs)
- grep text: `shutdown reason ::@ time`
- Regex: `shutdown reason ::@ time (?P<start>\d{13})\s+(?P<mode>Shuttingdown|restarting) because of (?P<reason>[A-Z_]+)`
- Sample: `1793144189003: 8081850: PWR: C: 5922: 5922: shutdown reason ::@ time 1793144189001	Shuttingdown because of SHUTDOWN_FOR_IGNITION_OFF 	::  [103102600722_2026-10-28/power_mon.log]`
- Used by: Cyclic Reboot Time (max_uptime)

#### `shutdown_initiated`

- Service log: `power_mon`
- grep text: `Device Shutdown Initiated`
- Regex: `Device Shutdown Initiated \(defer_req_cnt=`
- Sample: `PWR: I: <pid>: <tid>: Device Shutdown Initiated (defer_req_cnt=...)  [power_mon.log; 5-8 s before system_execute shutdown]`
- Used by: Device Shutdown Time

#### `shutdown_exec`

- Service log: `power_mon`
- grep text: `system_execute cmd: `
- Regex: `SYS_U: I: \d+: \d+: system_execute cmd: (?P<cmd>shutdown now|poweroff|reboot)\b`
- Sample: `1793144916027: 8808874: SYS_U: I: 5922: 6636: system_execute cmd: shutdown now  [103102600722_2026-10-28/power_mon.log]`
- Used by: Device Shutdown Time

#### `pm_start`

- Service log: `power_mon`  (also in critical `_c` logs)
- grep text: `STARTING POWER MONITOR`
- Regex: `#####STARTING POWER MONITOR#####`
- Sample: `1793239003547: 695: PWR: E: 5973: 5973: #####STARTING POWER MONITOR#####  [log_prod/103102600722/2026-10-29/power_mon]`
- Used by: Power Monitor Start after reboot, Time taken to Reboot

#### `ota_size`

- Service log: `otacheck/updater`
- grep text: `OTA package size in bytes`
- Regex: `OTA package size in bytes = (?P<bytes>\d+)`
- Sample: `2026-10-27 12:54:55,365 - __main__ - INFO - OTA package size in bytes = 45930165, OTA required size in KB = 491643.201171875, space left in device in KB = 10765052  [log_prod/6603014391/2026-10-27/otacheck]`
- Used by: OTA Package Size

#### `sm_update_time`

- Service log: `service_mon`  (also in critical `_c` logs)
- grep text: `updateTime: `
- Regex: `updateEngine : \d{13} : 210003 : -?\d+ : (?P<version>\S+) \| updateTime: (?P<s>[\d.]+)`
- Sample: `1793107036850: 1710: SM: E: 9632: 9632: Service error: updateEngine : 1793107036849 : 210003 : -1 : 4.6.17.rc.1 | updateTime: 71.914 : 3867089  [log_prod/6603014391/2026-10-27/service_mon]`
- Used by: OTA Update time

#### `updater_update_time`

- Service log: `updater`
- grep text: `OTA updateTime: `
- Regex: `OTA updateTime: (?P<s>[\d.]+) sec`
- Sample: `2026-10-27 13:17:16,849 - __main__ - INFO - OTA updateTime: 71.914 sec  [log_prod/6603014391/2026-10-27/updater]`
- Used by: OTA Update time

#### `aon_sm`

- Service log: `service_mon`  (also in critical `_c` logs)
- grep text: `AON Ver: `
- Regex: `: 41015 : -?\d+ : AON Ver: (?P<ver>0x[0-9A-Fa-f]+)`
- Sample: `1792452409825: 4439: SM: E: 5823: 5823: Service error: APM : 1792452409825 : 41015 : 27 : AON Ver: 0x1B : 27742  [103182502300_2026-10-20/service_mon_c.log]`
- Used by: AON Firmware Version

#### `aon_apm`

- Service log: `apm`
- grep text: `get_aon_application_version returns`
- Regex: `get_aon_application_version returns : (?P<ver>0x[0-9A-Fa-f]+)`
- Sample: `1793148920591: 9093: DEVB3: I: 5929: 5929: get_aon_application_version returns : 0x1b  [103102600722_2026-10-28/apm.log]`
- Used by: AON Firmware Version

#### `obd_fw`

- Service log: `obd`
- grep text: `adapter fw ver : `
- Regex: `adapter fw ver : (?P<ver>\S+)`
- Sample: `1793126072623: 6728441: J1939: I: 6207: 6207: adapter fw ver : SP4.4  [6603001461_2026-10-28/obd.log]`
- Used by: OBD Firmware

#### `band_mask`

- Service log: `conn_mgr`
- grep text: `LTE band pref from device is`
- Regex: `LTE band pref from device is (?P<mask>[0-9a-fA-F]+)`
- Sample: `1793147286093: 2785: CONN_MGR_UTIL_SIERRA: I: 14620: 15144: LTE band pref from device is 300080a  [6603001461_2026-10-28/conn_mgr.log]`
- Used by: Bands Enabled

#### `band_ext`

- Service log: `conn_mgr`
- grep text: `LTE band pref ext from device`
- Regex: `LTE band pref ext from device bits_(?P<range>1_64|65_128) is (?P<mask>[0-9a-fA-F]+)`
- Sample: `1793147286093: 2785: CONN_MGR_UTIL_SIERRA: I: 14620: 15144: LTE band pref ext from device bits_65_128 is 42  [6603001461_2026-10-28/conn_mgr.log]`
- Used by: Bands Enabled

#### `updater_start`

- Service log: `updater`
- grep text: `Starting OTA update`
- Regex: `Starting OTA update\s*::`
- Sample: `2026-10-27 13:16:04,933 - __main__ - INFO - ::====================::Starting OTA update ::====================::  [log_prod/6603014391/2026-10-27/updater]`
- Used by: Connection Manager - LTE Outage during OTA Update

#### `updater_stop`

- Service log: `updater`
- grep text: `-c stop -n bagheera`
- Regex: `Command from file\s+\d+ = sudo /usr/bin/nd_service\.sh -c stop -n bagheera\b`
- Sample: `2026-10-27 13:16:19,224 - __main__ - INFO - Command from file  2 = sudo /usr/bin/nd_service.sh -c stop -n bagheera -p /home/ubuntu/.nddevice/4.6.17.rc.1/service/  [log_prod/6603014391/2026-10-27/updater]`
- Used by: During OTA Update

#### `sm_cyclic`

- Service log: `service_mon`  (also in critical `_c` logs)
- grep text: `Reboot - Cyclic`
- Regex: `: 40016 : -?\d+ : Reboot - Cyclic`
- Sample: `1793568222759: 174564909: SM: E: 3732: 3732: Service error: PWR : 1793568222758 : 40016 : 0 : Reboot - Cyclic : 174608598  [log_prod/6603078588/2026-11-01/service_mon]`
- Used by: Device Cyclic Reboot

#### `iot_reboot`

- Service log: `awsiot`
- grep text: `Received command: reboot-phone`
- Regex: `Received command: reboot-phone`
- Sample: `1793698112367: 1255630: IOT-PRSR: I: 3778: 3778: Received command: reboot-phone  [264080392_2026-11-03/awsiot.log]`
- Used by: Device Reboot from AWS IoT / Cloud Command

#### `cam_crash`

- Service log: `ndcentral`  (also in critical `_c` logs)
- grep text: `is crashed`
- Regex: `NDC: E: \d+: \d+: cam_num (?P<cam>\d+) is crashed`
- Sample: `1793577098083: 106579347: NDC: E: 4365: 4598: cam_num 0 is crashed  [log_prod/6603004626/2026-11-02/ndcentral]`
- Used by: Service Restart due to Camera Crash, Outward Camera Crash Count - Total, Outward Camera Crash Count %, Inward Camera Crash Count - Total, Inward Camera Crash Count %, Left Camera Crash Count - Total, Left Camera Crash Count %, Right Camera Crash Count - Total, Right Camera Crash Count %, MDVR 4 Camera Crash Count - Total, MDVR 4 Camera Crash Count %, MDVR 5 Camera Crash Count - Total, MDVR 5 Camera Crash Count %, MDVR 6 Camera Crash Count - Total, MDVR 6 Camera Crash Count %, MDVR 7 Camera Crash Count - Total, MDVR 7 Camera Crash Count %, DMS Camera Crash Count - Total, DMS Camera Crash Count %

#### `record_start`

- Service log: `ndcentral`
- grep text: `RECORD_START for cam num`
- Regex: `RECORD_START for cam num (?P<cam>\d+) with session_cnt \d+ received @ epoch_time_ms: (?P<epoch>\d{13})`
- Sample: `1793577114647: 3807: NDC: I: 6651: 6651: RECORD_START for cam num 0 with session_cnt 0 received @ epoch_time_ms: 1793577114619, pts_time: 0  [log_prod/6603004626/2026-11-02/ndcentral]`
- Used by: Service Restart due to Camera Crash

#### `sm_prev_shutdown`

- Service log: `service_mon`  (also in critical `_c` logs)
- grep text: `Previous Shutdown:`
- Regex: `: 40105 : -?\d+ : Previous Shutdown:\s*(?P<reason>\S+) \(Speed`
- Sample: `1793058577350: 7676: SM: E: 5855: 5855: Service error: PWR : 1793058577350 : 40105 : 53384 : Previous Shutdown: :DISCHARGE_STATE:RESET_STATE:SHUTDOWN_STATE:SUPERCAP_DISCHARGE:POWER_ON_RESET (Speed: 0.000000) : 30331  [log_prod/103352600191/2026-10-27/service_mon]`
- Used by: Device Abrupt Power Cycle (Without Supercap), Device Abrupt Power Cycle (With Supercap), Device Reboot time after Abrupt Power Cycle (till video recording start)

#### `conn_session_lost`

- Service log: `conn_mgr`
- grep text: `SessionStateCallback:DISCONNECTED`
- Regex: `SessionStateCallback:DISCONNECTED`
- Sample: `CONN_MGR_CBK: I: <pid>: <tid>: SessionStateCallback:DISCONNECTED  [conn_mgr.log]`
- Used by: Connection Manager - LTE Outage during OTA Update

#### `conn_exit`

- Service log: `conn_mgr`
- grep text: `Device disconnected : Exiting App`
- Regex: `Device disconnected : Exiting App`
- Sample: `1793103098100: 2155303: CONN_MGR_MAIN: E: 29430: 29509: Device disconnected : Exiting App  [log_prod/6603014391/2026-10-27/conn_mgr]`
- Used by: Connection Manager - LTE Outage during OTA Update, Connection Manager - LTE Outage during Device Disconnection

#### `data_connected`

- Service log: `conn_mgr`
- grep text: `Data session connected`
- Regex: `Success : Data session connected`
- Sample: `1793107052129: 6497: CONN_MGR_MAIN: I: 11149: 11149: Success : Data session connected  [log_prod/6603014391/2026-10-27/conn_mgr]`
- Used by: Connection Manager - LTE Outage during OTA Update, Connection Manager - LTE Outage during Device Disconnection

#### `pushed_file`

- Service log: `circ_buff`
- grep text: `Pushed file `
- Regex: `Pushed file (?P<cam>\d+)_(?P<name>trip\S+?_y)(?P<ext>\.mp4\.ld\.mp4|\.mp4|\.zip) with size (?P<size>\d+) to file_size_check_queue`
- Sample: `1793144343499: 8231562: CB: I: 6240: 6240: Pushed file 0_trip1bbf_part00f1c6_33.8681_-117.9309_0.0_1793144282968_y.mp4 with size 38537408 to file_size_check_queue  [103102600722_2026-10-28/circ_buff.log]`
- Used by: Outward Video - File Size - HD, Inward Video - File Size - HD, Left Video - File Size - HD, Right Video - File Size - HD, MDVR 4 Video - File Size - HD, MDVR 5 Video - File Size - HD, MDVR 6 Video - File Size - HD, MDVR 7 Video - File Size - HD, DMS Video - File Size - HD, Outward Video - File Size - LD, Inward Video - File Size - LD, MDVR 4 Video - File Size - LD, MDVR 5 Video - File Size - LD, MDVR 6 Video - File Size - LD, MDVR 7 Video - File Size - LD, DMS Video - File Size - LD, HD Obs - Avg size of file, Obs File Size (One zip with valid GPS)

#### `frame_count`

- Service log: `ndcentral/cam_rec`
- grep text: `Frame count rec`
- Regex: `CAM(?P<cam>\d+): I: \d+: \d+: Frame count rec(?:ei|ie)ved: (?P<frames>\d+)`
- Sample: `1793144465466: 8358899: CAM0: I: 6032: 7147: Frame count recieved: 150   [103102600722_2026-10-28/ndcentral.log] (every 5 s per camera; summed per 10 min in the scan)`
- Reduced in the scan: `frames` summed per 10 min and camera
- Used by: Outward Video - FPS - HD, Inward Video - FPS - HD, Left Video - FPS - HD, Right Video - FPS - HD, MDVR 4 Video - FPS - HD, MDVR 5 Video - FPS - HD, MDVR 6 Video - FPS - HD, MDVR 7 Video - FPS - HD, DMS Video - FPS - HD, Outward Video - FPS - LD, Inward Video - FPS - LD

#### `storage_info`

- Service log: `deviceHealthClient`
- grep text: `"storage_info"`
- Regex: `Received message: \{"storage_info": (?=(?:.*"no_of_hq": \{(?P<hq>[^}]*)\})?)(?=(?:.*"no_of_lq": \{(?P<lq>[^}]*)\})?)(?=(?:.*"no_of_obs": "(?P<obs>[^"]*)")?)`
- Sample: `2026-10-27 23:33:25,748 - INFO    - deviceHealthClient        - Received message: {"storage_info": {"timestamp": 1793144002970, "sys_uptime": 132, ... "no_of_hq": {"0": "216/7694.6", ...}, "no_of_lq": {"0": "7639/43084.4", ...}, "no_of_obs": "7594/..."  [103102600722_2026-10-28/deviceHealthClient.log] (values are "file count/MiB")`
- Used by: Outward - LD File Size (Average), Inward - LD File Size (Average), Outward - HD File Size (Average), Inward - HD File Size (Average), Max HD Obs files can be stored - File Count, Total Outward files in eMMC, Total Inward files in eMMC, Total Left files in eMMC, Total Right files in eMMC, Total MDVR 4 files in eMMC, Total MDVR 5 files in eMMC, Total MDVR 6 files in eMMC, Total MDVR 7 files in eMMC, Total DMS files in eMMC, Obs files in eMMC, eMMC - Storage Hours

#### `obs_ld_size`

- Service log: `inference`
- grep text: `obsZipDestFile size`
- Regex: `obsZipDestFile size: b'\S+/observations/\S+?\.zip' -- (?P<size>\d+)`
- Sample: `2026-10-27 18:30:41,982 - root - INFO - obsZipDestFile size: b'/home/ubuntu/.nddevice/observations/0_trip5689_part05d3c2_33.3346_-117.2435_0.0_1793125756531_y_05468d43cec9da91e04814043039277c.zip' -- 17296  [6603001461_2026-10-28/inference.log]`
- Used by: LD Obs - Avg size of file

#### `disk_free`

- Service log: `svc`
- grep text: `Free space`
- Regex: `DISK: I: \d+: \d+: Free space (?:at / )?: (?P<bytes>\d+)$`
- Sample: `1793141592332: 5485201: DISK: I: 5953: 6208: Free space : 3740762112  [103102600722_2026-10-28/svc.log]`
- Used by: Storage Size

#### `disk_used`

- Service log: `diagnostic`
- grep text: `used memory of /dev/mmcblk0 is`
- Regex: `used memory of /dev/mmcblk0 is (?P<used>[\d.]+)$`
- Sample: `1793143416160: 7307740: DIAG: I: 5914: 6141: used memory of /dev/mmcblk0 is 9747  [103102600722_2026-10-28/diagnostic.log] (MiB; some builds log a percentage: 46.503920)`
- Used by: Storage Size

#### `waf`

- Service log: `diagnostic`
- grep text: `Waf is `
- Regex: `WAF_U: I: \d+: \d+: Waf is (?P<waf>[\d.]+)`
- Sample: `1793148955065: 41525: WAF_U: I: 6005: 7835: Waf is 1.747442  [103102600722_2026-10-28/diagnostic.log] (1st of a cycle = internal eMMC, 2nd = external)`
- Used by: External eMMC, Internal eMMC

#### `bt_ignition_on`

- Service log: `btfv`
- grep text: `IGNITION ON received`
- Regex: `BTMAN: I: \d+: \d+: (?:\[GEN\] )?IGNITION ON received`
- Sample: `1793684844100: 7236: BTMAN: I: 3747: 3747: IGNITION ON received  [264080392_2026-11-03/btfv.log]`
- Used by: Ignition ON Detected, Scan for Installer App Starts, Scan for Installer App Stops, Ignition ON Detected, Advertising Started, Scan for DriveriApp / BT Beacon Starts, Scan for DriveriApp / BT Beacon Stops

#### `installer_scan_start`

- Service log: `btfv`
- grep text: `RegisterInstallerAppFilter`
- Regex: `Inside: RegisterInstallerAppFilter`
- Sample: `BTMAN: I: <pid>: <tid>: Inside: RegisterInstallerAppFilter  [264080392_2026-11-03/btfv.log] (0-17 ms after IGNITION ON)`
- Used by: Scan for Installer App Starts

#### `installer_scan_stop`

- Service log: `btfv`
- grep text: `InstallerApp timeout cb triggered` or `ClearInstallerAppFilter`
- Regex: `InstallerApp timeout cb triggered|Inside: ClearInstallerAppFilter`
- Sample: `1793684964356: 127492: BTMAN: I: 3747: 4022: InstallerApp timeout cb triggered  [264080392_2026-11-03/btfv.log]`
- Used by: Scan for Installer App Stops

#### `driver_adv_start`

- Service log: `btfv`
- grep text: `Starting Driver Login Advertisement`
- Regex: `Starting Driver Login Advertisement`
- Sample: `1793684844137: 7273: BTMAN: I: 3747: 3747: Starting Driver Login Advertisement  [264080392_2026-11-03/btfv.log]`
- Used by: Advertising Started

#### `driver_scan_start`

- Service log: `btfv`
- grep text: `RegisterDriverLoginLegacyFilter`
- Regex: `Inside: RegisterDriverLoginLegacyFilter`
- Sample: `1793684844136: 7272: BTMAN: I: 3747: 3747: Inside: RegisterDriverLoginLegacyFilter  [264080392_2026-11-03/btfv.log]`
- Used by: Scan for DriveriApp / BT Beacon Starts

#### `driver_scan_stop`

- Service log: `btfv`
- grep text: `DriverLogin legacy scan complete cb triggered`
- Regex: `DriverLogin legacy scan complete cb triggered`
- Sample: `1793684874335: 37471: BTMAN: I: 3747: 4022: DriverLogin legacy scan complete cb triggered  [264080392_2026-11-03/btfv.log]`
- Used by: Scan for DriveriApp / BT Beacon Stops

#### `alerts_contains`

- Service log: `inference`
- grep text: `alerts.json contains`
- Regex: `(?<!i)alerts\.json contains (?P<n>\d+) alert\(s\)`
- Sample: `2026-10-28 20:37:23,630 - root - INFO - alerts.json contains 1 alert(s)  [103102600722_2026-10-28/inference.log]`
- Used by: Total Alerts Detected

#### `upload_state`

- Service log: `inference`
- grep text: `to UPLOAD_STATE`
- Regex: `Moving \S*/\d_(?P<key>trip\S+?_y)\.STATE to UPLOAD_STATE\s+alerts found`
- Sample: `2026-10-28 20:37:23,649 - root - INFO - Moving /home/iriscli/ND_INPUT/0_trip1bf3_part00f3cb_34.0137_-118.0565_0.0_1793219747843_y.STATE to UPLOAD_STATE   alerts found, exit code 0  [103102600722_2026-10-28/inference.log]`
- Used by: Total Unique, File Recording Start time, Inference / Vision Alert Detection / Processing

#### `lla_msg_send`

- Service log: `outwardAnalyticsClient/inwardAnalyticsClient/dmsAnalyticsClient/inertialAnalyticsClient`
- grep text: `msg_send: `
- Regex: `lla_handler\s+- msg_send: (?=.*"videoName": "\d_(?P<key>trip[^"]+?_y)(?:\.mp4)?")(?=.*"uuid": "(?P<uuid>[0-9a-f]{32})")(?=(?:.*"end_timestamp": (?P<end>\d{13}))?)(?=(?:.*"event_code": "(?P<code>[\d.]+)")?)`
- Sample: `2026-10-28 14:36:09,025 - INFO    - src.lla_handler           - msg_send: {"inference_data": {"alerts_data": {"alerts": [{"speed_info": 68.03, "end_timestamp": 1793198167449, "event_code": "1.0.0.0", "uuid": "4294e556792744d8bf466d02c4d1f98f", ... "videoName": "0_trip568d_part05d441_32.9981_-117.0829_0.0_1793198116548_y.mp4"  [6603001461_2026-10-28/outwardAnalyticsClient.log]`
- Used by: File Recording Start time, Inference / Vision Alert Detection / Processing, File Recording Start time, NDCentral LLA creation, NDCentral LLA notifying to uploader, Total Time Taken for upload (from alert detection)

#### `ialerts_found`

- Service log: `inference_inertial`
- grep text: `ialerts.json found at`
- Regex: `ialerts\.json found at \S*/\d_(?P<key>trip\S+?_y)/ialerts\.json, size=(?P<size>\d+) bytes`
- Sample: `2026-11-02 19:17:21,231 - root - INFO - ialerts.json found at /home/iriscli/ND_OUTPUT/0_trip2b1f_part088982_30.4286_-89.0788_0.0_1793635811955_y/ialerts.json, size=415 bytes  [log_prod/6603007848/2026-11-03/inference_inertial] (size 31 = empty list)`
- Used by: File Recording Start time, Inertial Alert Detection

#### `eventdata_summary`

- Service log: `unifieduploader`
- grep text: `Summary file size`
- Regex: `UPL: I: \d+: (?P<tid>\d+): Summary file size: \d+ name:\s*\S*/\d_(?P<key>trip\S+?_y)_summary\.json\.7z`
- Sample: `1793198232440: 1561332: UPL: I: 3381: 3751: Summary file size: 328107 name:	/dev/shm/0_trip568d_part05d441_32.9981_-117.0829_0.0_1793198116548_y_summary.json.7z  [6603001461_2026-10-28/unifieduploader.log]`
- Used by: Alert Notification to the Cloud - API Call Triggering

#### `eventdata_call`

- Service log: `unifieduploader`
- grep text: `upload/eventdata`
- Regex: `UPL: I: \d+: (?P<tid>\d+): Calling service: \S+/upload/eventdata\b`
- Sample: `1793198232519: 1561411: UPL: I: 3381: 3751: Calling service: https://idms.netradyne.com/restserver/api/v1/upload/eventdata  [6603001461_2026-10-28/unifieduploader.log]`
- Used by: Alert Notification to the Cloud - API Call Triggering

#### `eventdata_ok`

- Service log: `unifieduploader`
- grep text: `Upload successful for `
- Regex: `Upload successful for \d_(?P<key>trip\S+?_y)\.mp4$`
- Sample: `1793219851656: 1559795: UPL: I: 5887: 6361: Upload successful for 0_trip1bf3_part00f3cb_34.0137_-118.0565_0.0_1793219747843_y.mp4  [103102600722_2026-10-28/unifieduploader.log]`
- Used by: Total Alerts Event Cards published, Alert Notification to the Cloud - API Call Successful, upload-eventdata - Retry Duration

#### `eventdata_fail`

- Service log: `unifieduploader`
- grep text: `Upload failed for `
- Regex: `Upload failed for \d_(?P<key>trip\S+?_y)\.mp4$`
- Sample: `1793268143193: 261371: UPL: I: 1305: 2748: Upload failed for 0_trip0003_part00000d_91.0000_181.0000_0.0_1793267415178_y.mp4  [105322600008_2026-10-29/unifieduploader.log]`
- Used by: upload-eventdata - Retry Duration

#### `vod_req`

- Service log: `unifieduploader`
- grep text: `VOD req received`
- Regex: `VOD req received\. Id: \d+, vod_id: (?P<vod>\S+) , video: \S*/(?P<cam>\d)_(?P<key>trip\S+?_y)\.mp4`
- Sample: `1793219859559: 1567698: UPL: I: 5887: 5887: VOD req received. Id: 1023368905, vod_id: vod-v2-1023368905-589283893506-1 , video: /media/data/nd_sdcard/0_trip1bf3_part00f3cb_34.0137_-118.0565_0.0_1793219747843_y.mp4 , cancelled: 0, priority: 5  [103102600722_2026-10-28/unifieduploader.log]`
- Used by: Upload Video Request Received from Cloud - Outward, Upload Video Request Received from Cloud - Inward, Upload Video Request Received from Cloud - Left, Upload Video Request Received from Cloud - Right, Upload Video Request Received from Cloud - MDVR 4, Upload Video Request Received from Cloud - MDVR 5, Upload Video Request Received from Cloud - MDVR 6, Upload Video Request Received from Cloud - MDVR 7, Upload Video Request Received from Cloud - DMS

#### `video_ok`

- Service log: `unifieduploader`
- grep text: `Upload successful for video: `
- Regex: `Upload successful for video: \S*/(?P<cam>\d)_(?P<key>trip\S+?_y)\.mp4`
- Sample: `1793219883071: 1591210: UPL: I: 5887: 6362: Upload successful for video: /media/data/nd_sdcard/0_trip1bf3_part00f3cb_34.0137_-118.0565_0.0_1793219747843_y.mp4  [103102600722_2026-10-28/unifieduploader.log]`
- Used by: Total Alerts Video uploaded - Cam 0, Total Alerts Video uploaded - Cam 1, Total Alerts Video uploaded - Cam 2, Total Alerts Video uploaded - Cam 3, Total Alerts Video uploaded - Cam 4 (MDVR 4), Total Alerts Video uploaded - Cam 5 (MDVR 5), Total Alerts Video uploaded - Cam 6 (MDVR 6), Total Alerts Video uploaded - Cam 7 (MDVR 7), Total Alerts Video uploaded - Cam 8 (DMS), Upload Video Successful - Outward, Upload Video Successful - Inward, Upload Video Successful - Left, Upload Video Successful - Right, Upload Video Successful - MDVR 4, Upload Video Successful - MDVR 5, Upload Video Successful - MDVR 6, Upload Video Successful - MDVR 7, Upload Video Successful - DMS, Total Time Taken for upload (from alert detection)

#### `event_code`

- Service log: `inference/inference_inertial`
- grep text: `is_alert=`
- Regex: `eventutil - INFO - \d+: is_alert=[01]\s+(?P<code>\d+(?:\.\d+)*) \[.*\] uuid=(?P<uuid>[0-9a-f]+)`
- Sample: `2026-10-28 20:37:21,324 - src.eventutil - INFO - 9: is_alert=1  101.1.3.1 [ 38.1s to  41.5s] uuid=d80f856542d846418a8ad4988a276fc1  [103102600722_2026-10-28/inference.log] (first line per uuid kept)`
- Reduced in the scan: first line per `uuid` only
- Used by: Analytics KPIs (event codes)

#### `obs_req`

- Service log: `unifieduploader`
- grep text: `Upload Observations Request Received`
- Regex: `UPL: I: \d+: (?P<tid>\d+): Upload Observations Request Received (?P<rt>\d{16})`
- Sample: `1793144349601: 8237664: UPL: I: 5887: 6370: Upload Observations Request Received 1793144349588391  [103102600722_2026-10-28/unifieduploader.log] (rt = request time, epoch us)`
- Used by: Upload Request for Obs zip - Received, Files adding into zip - Completion (10 files), Uploading Obs Zip - Start, Uploading Obs Zip - Successful

#### `obs_zip_start`

- Service log: `unifieduploader`
- grep text: `Creating observations zip file at once`
- Regex: `UPL: I: \d+: (?P<tid>\d+): Creating observations zip file at once!`
- Sample: `UPL: I: <pid>: <tid>: Creating observations zip file at once!  [unifieduploader.log] (same ms as the last "adding obs json zip cmd")`
- Used by: Files adding into zip - Completion (10 files), Creating Obs Zip

#### `obs_zip_done`

- Service log: `unifieduploader`
- grep text: `Obs zip file created`
- Regex: `UPL: I: \d+: (?P<tid>\d+): Obs zip file created!`
- Sample: `UPL: I: <pid>: <tid>: Obs zip file created!  [unifieduploader.log]`
- Used by: Creating Obs Zip

#### `obs_call`

- Service log: `unifieduploader`
- grep text: `upload/observations`
- Regex: `UPL: I: \d+: (?P<tid>\d+): Calling service: \S+/upload/observations\b`
- Sample: `UPL: I: <pid>: <tid>: Calling service: https://idms.netradyne.com/restserver/api/v1/upload/observations  [unifieduploader.log]`
- Used by: Uploading Obs Zip - Start

#### `obs_uploaded`

- Service log: `unifieduploader`
- grep text: `File uploaded. Deleting`
- Regex: `UPL: I: \d+: (?P<tid>\d+): File uploaded\. Deleting \d+`
- Sample: `UPL: I: <pid>: <tid>: File uploaded. Deleting 1  [unifieduploader.log]`
- Used by: Uploading Obs Zip - Successful, upload-observationdata - Retry Duration

#### `obs_fail`

- Service log: `unifieduploader`
- grep text: `Failed to fetch url`
- Regex: `UPL: I: \d+: (?P<tid>\d+): ERROR: Failed to fetch url \(\S+/upload/observations\)`
- Sample: `1793213578240: 56922: UPL: I: 5984: 6412: ERROR: Failed to fetch url (https://idms.netradyne.com/restserver/api/v1/upload/observations) - curl said: Couldn't resolve host name  [unifieduploader.log]`
- Used by: upload-observationdata - Retry Duration

#### `retry_details`

- Service log: `unifieduploader`
- grep text: `Details: type - msg type`
- Regex: `\w+ Details: type - msg type: (?P<type>\d+) msg level`
- Sample: `1793268120515: 238693: UPL: I: 1305: 2748: PS Details: type - msg type: 100 msg level: 0  [105322600008_2026-10-29/unifieduploader.log] (logged right after "Retrying... retry_count: n"; 100 eventdata, 101 video, 110 observations)`
- Used by: upload-eventdata - Retry Count, upload-observationdata - Retry Count

#### `vod_attempt_failed`

- Service log: `unifieduploader`  (also in critical `_c` logs)
- grep text: `VOD upload attempt failed`
- Regex: `VOD upload attempt failed: (?P<vod>vod-[^\s,]+)`
- Sample: `1793276679063: 1227729: UPL: E: 1211: 2675: VOD upload attempt failed: vod-v2-19665441-2252965591-0, vod_id  [105322600008_2026-10-29/unifieduploader.log]`
- Used by: upload-video - Retry Count, upload-video - Retry Duration

#### `vod_exhausted`

- Service log: `unifieduploader`  (also in critical `_c` logs)
- grep text: `Exhausted all upload retries`
- Regex: `Exhausted all upload retries for (?P<vod>vod-\S+)`
- Sample: `1792455673698: 2912304: UPL: C: 5963: 6390: Exhausted all upload retries for vod-v2-19523042-2234900699-0 : 6. Max retry: 6  [103182502300_2026-10-20/unifieduploader_c.log]`
- Used by: upload-video - Retry Count

#### `vod_deleted`

- Service log: `unifieduploader`
- grep text: `deleted VOD upload req`
- Regex: `deleted VOD upload req \S+ - (?P<vod>vod-\S+) from DB`
- Sample: `1793219883086: 1591225: UDB: I: 5887: 6362: deleted VOD upload req /media/data/nd_sdcard/0_trip1bf3_part00f3cb_34.0137_-118.0565_0.0_1793219747843_y.mp4 - vod-v2-1023368905-589283893506-1 from DB. msg_type = 101  [103102600722_2026-10-28/unifieduploader.log]`
- Used by: upload-video - Retry Duration

#### `videolist_failed`

- Service log: `circ_buff`
- grep text: `collect_notify_videolist is FAILED`
- Regex: `collect_notify_videolist is FAILED`
- Sample: `1793264997847: 107142: CB: I: 4620: 5443: collect_notify_videolist is FAILED  [105322600008_2026-10-29/circ_buff.log]`
- Used by: videolist - Retry Count, videolist - Retry Duration

#### `videolist_ok`

- Service log: `circ_buff`
- grep text: `collect_notify_videolist is succeded`
- Regex: `collect_notify_videolist is succeded`
- Sample: `CB: I: <pid>: <tid>: collect_notify_videolist is succeded  [circ_buff.log]`
- Used by: videolist - Retry Duration

#### `health_post_failed`

- Service log: `health`
- grep text: `curl exited`
- Regex: `curl_with_health: curl exited (?P<code>\d+)`
- Sample: `2026-10-28 07:42:02,449 - health - ERROR - curl_with_health: curl exited 6: % Total    % Received ...  [6603001461_2026-10-28/health.log]`
- Used by: upload-device-status - Retry Count, upload-device-status - Retry Duration

#### `health_ok`

- Service log: `health`
- grep text: `Upload of Health Stats successful`
- Regex: `Upload of Health Stats successful`
- Sample: `2026-10-27 18:30:14,6xx - health - INFO - Upload of Health Stats successful  [6603001461_2026-10-28/health.log]`
- Used by: upload-device-status - Retry Duration

#### `ndc_lla_message`

- Service log: `ndcentral`  (also in critical `_c` logs)
- grep text: `lla_message :{`
- Regex: `NDC: C: \d+: \d+: lla_message :\{`
- Sample: `1793198169230: 1495306: NDC: C: 3808: 5800: lla_message :{"inference_data": {"alerts_data": {"alerts": [{"speed_info": 68.029000458893947, "end_timestamp": 1793198167449, ...  [6603001461_2026-10-28/ndcentral.log] (cut at ~300 chars, so paired by time)`
- Used by: NDCentral LLA creation

#### `lla_hs_add`

- Service log: `unifieduploader`
- grep text: `"uploadadd"`
- Regex: `"lla_(?P<uuid>[0-9a-f]{32})_(?P<n>\d+)": \{"uploadadd": (?P<add>\d{13})\}`
- Sample: `1793198169246: 1498138: UPL: I: 3381: 3625: sending msg to hs--: {"session": "0_trip568d_part05d441_32.9981_-117.0829_0.0_1793198116548_y", "alert_info": {"lla_4294e556792744d8bf466d02c4d1f98f_0": {"uploadadd": 1793198169244}}}  [6603001461_2026-10-28/unifieduploader.log]`
- Used by: NDCentral LLA notifying to uploader

#### `lla_hs_result`

- Service log: `unifieduploader`
- grep text: `"uploadstart"`
- Regex: `"lla_(?P<uuid>[0-9a-f]{32})_(?P<n>\d+)": \{"uploadstart": (?P<start>\d{13}), "uploadend": (?P<end>\d{13}), "status": "(?P<status>pass|fail)"`
- Sample: `1793149279280: 2060651: UPL: I: 3391: 3684: sending msg to hs--: {"session": "0_trip568a_part05d3f9_33.1186_-117.1010_0.0_1793149263671_y", "alert_info": {"lla_2580cfa0a3204cefbac94ea93ae879a0_0": {"uploadstart": 1793149278811, "uploadend": 1793149279280, "status": "pass", ...  [6603001461_2026-10-28/unifieduploader.log]`
- Used by: Uploader LLA Notification to the Cloud - API Call Triggering, Uploader LLA Notification to the Cloud - API Call Successful, Total Time Taken for upload (from alert detection), LLA retry count, LLA retry duration
