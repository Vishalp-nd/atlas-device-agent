---
name: obd-critical-errors
description: "Use when analyzing OBD-family critical events CODE=95001 (can detection error) or CODE=95013 (ADC reading failed) from J1939/OBD."
---

# OBD Critical Errors

Apply the shared narrowing method and confidence rubric in `critical-event-query-triage/SKILL.md` to every tuple below.

## `SM_E_OBD_CAN_DETECT_ERR` (CODE=95001)

**Identify:** PROCESS=`J1939`, DESCRIPTION=`can detection error`, `CODE_AUX` sent as `NDService::UNUSED_ERR_AUX_CODE` — observed deployed value is always `-1` (no subtype carried).

**In plain terms:** The device can't see any activity on the vehicle's data wiring (CAN bus) at all. This is usually just the ignition/engine being off, but can also mean a wiring, connector, or hardware problem is stopping the device from reading vehicle data. Don't read anything into other events that happen to appear near this one in the log — checked against real device data, this fires so often (139 times in one day on the sampled device) on a device that reboots every few minutes that unrelated per-boot log lines land "nearby" it purely by coincidence, not causation.

**Why it triggered:** Emitted in the `MCS_NO_CAN_BUS_ACTIVITY` case in `obd_main.cpp` — the OBD/J1939 stack resolved controller state to "no detectable CAN bus activity" (not a parse/protocol error) and updates health state (`setCanError("NO_CAN_BUS_ACTIVITY")`, `setCanBaud(...)`). Practical causes: vehicle CAN bus silent because ignition/engine is off, CAN wiring/harness issue, transceiver/physical-layer issue, wrong baud/stack selection, or a controller-side comms problem preventing valid CAN activity detection. Source: `nd_vdm/obd/service/src/obd_main.cpp` (sibling `nd_vdm` repo, not `nd_device_services`). Sample row: `95001, -1, "can detection error", ..., J1939`.

**Evidence** (log-validated) in `service_mon.log`, checked on device `3633055257` (139 occurrences in one day; `reboot.log` shows this device rebooting every few minutes to tens of minutes all day):

Tuple: `PROCESS=J1939, CODE=95001, CODE_AUX=-1, DESCRIPTION="can detection error"`

- `98002` (34 occurrences) and `130005` (35 occurrences, aux=24) do often land within minutes of a 95001 cluster, but neither is an instability signal on inspection: `130005` is a routine periodic eMMC health report, and `98002`'s description field is just the modem firmware version string logged once at `CONN_MGR_MAIN` startup. Both fire once per boot as normal startup housekeeping and cluster near 95001 only because this device is stuck in a frequent reboot loop — not because of any causal link.
- Only 2 `30000`/`30023` (camera crash) events exist in the whole log, and neither supports a CAN-failure-triggers-camera-crash story: one is an independent boot-time camera-pipeline fault (`cam_rec.log`: "No frames generated from camera, do error callback" — no CAN/J1939 reference at all), and the other explicitly shows the *reverse* causality — `PWR: 40025 "Reboot Initiated - Camera Crash"` fires 96ms after the camera crash, i.e. the camera crash triggers a reboot, it does not follow from a CAN failure.

So on this device, treat `95001` as an isolated per-boot CAN-silence signal. Do not infer a causal link to nearby modem-startup or camera-crash events without independently re-confirming that link on the specific rows in question.

**Confidence:** `High` on the tuple identification — process/description/aux match is exact and unambiguous. `Low` on any claimed correlation to `98002`, `130005`, `30000`, or `30023` in the same window — checked against real logs and found to be coincidental boot-cadence clustering, or in one case, reversed causality.

## `SM_E_OBD_ADC_ERROR_CODE` (CODE=95013)

**Identify:** PROCESS=`J1939`, DESCRIPTION=`ADC reading failed Boot mode <n>`, `CODE_AUX` maps to the `ADC_READ_ERR_AUX_CODE` family in `obd_config.h` (`10005` = ADC read error; sibling values `10000`-`10004` cover CAN-disable/config-error subtypes — resolve aux against that enum before concluding).

**In plain terms:** The device repeatedly failed to read voltage/sensor data from the vehicle's controller, and when it checked why, the controller wasn't in its normal running state — it was stuck in a boot/recovery mode instead of operating normally.

**Why it triggered:** Emitted from `send_adc_data_to_pm()` after repeated failure of `request_periodic_adc_data()`. The code then reads controller boot state; if the controller is in a valid app mode (`app1`/`app2`) it keeps retrying silently, and only emits this event once ADC reads keep failing while boot mode is invalid. Practical causes: controller stuck in unexpected boot mode, firmware not reaching normal application state, SPI/controller communication issue, or controller reset/boot instability. Source: `nd_vdm/obd/service/src/obd_main.cpp`; aux enum in `nd_vdm/obd/service/inc/obd_config.h`. Sample row: `95013, 10005, "ADC reading failed Boot mode 255", ..., J1939`.

**Evidence** (log-validated, cross-device, deterministic) in `obd.log`/`obd_c.log` — verified programmatically across all 62 combined occurrences on 2 devices (61/62 order-confirmed; the 1 exception simply predates the start of an available log file due to rotation, not a real deviation):

Tuple: `PROCESS=J1939, CODE=95013, CODE_AUX=10005, DESCRIPTION="ADC reading failed Boot mode 255"`

1. `CAN: E: got wrong data length in func get_veh_adc_data` — ADC response from controller malformed.
2. `J1939: E: get veh adc data failed -1` — J1939 layer reports the read failed.
3. `J1939: E: adc voltage read timeout, did not receive in 5 sec` — timeout waiting for valid ADC data.
4. `J1939: E: Failed to get adc data` — ADC read declared failed.
5. `CAN: E: Unknown length received: <hex>` — a second malformed CAN read.
6. `J1939: E: controller not in valid mode, restarting, mode : FF` — controller in boot mode 255 (0xFF), not a valid app mode.
7. `service_mon` emits `95013 : 10005 : ADC reading failed Boot mode 255`.

This sequence is fully deterministic. Confirmed on 59 occurrences on device `264132748` and 3 occurrences on device `264095012` (62 total). Mode `FF` (255) means the controller never reached app1/app2.

**Confidence:** `High` when process is `J1939`, description matches this pattern, and aux resolves to `ADC_READ_ERR_AUX_CODE` (`10005`) — this is the one claim in this skill that fully survived log verification.
