---
name: ndcentral-critical-errors
description: "Use when analyzing ND Central critical events, specifically CODE=30023 (Camera LPM crash / SM_E_NDC_CAM_LPM_CRASH)."
---

# ND Central Critical Errors

Apply the shared narrowing method and confidence rubric in `critical-event-query-triage/SKILL.md` to every tuple below.

## `SM_E_NDC_CAM_LPM_CRASH` (CODE=30023)

**Identify:** PROCESS=`NDC`, DESCRIPTION=`Camera LPM crash`, `CODE_AUX` = camera index (e.g. `3`) — always map aux to physical camera position before concluding.

**In plain terms:** One of the cameras crashes and gets flagged during camera bring-up, and every confirmed occurrence happens within about 20 seconds of the whole device rebooting, right around an ignition on/off transition.

**Why it triggered:** The event name (`SM_E_NDC_CAM_LPM_CRASH`) and the `send_err_msg` call label this as a low-power-mode crash subtype, and the `DEVB3` I2C boot-status recheck succeeding (step 5 below) is what routes NDC into this subtype instead of the generic `SM_E_NDC_CAM_CRASH`. Source: `nd-central/common/central/nd_central.cpp`, `send_err_msg(SM_E_NDC_CAM_LPM_CRASH, i, "Camera LPM crash")`. However, checked against the raw device logs (not just the code/comment naming), none of the 9 confirmed occurrences across 3 devices show an `enter_lpm`/`exit_lpm`/suspend/resume/wake marker for the camera subsystem in the window before the crash. What the logs do show consistently: the crash fires ~17-22s after `power_mon.log` logs `Device has booted up` (i.e. the whole device/NDC process just restarted), with an `IGNITION ON` or `IGNITION OFF` event landing a few seconds before the crash in most cases. So the confirmed trigger context is camera-pipeline bring-up/tear-down during device boot/ignition transitions — the LPM sleep/wake mechanism implied by the name is not substantiated by log evidence and should not be asserted as fact.

**Evidence** (log-validated, cross-device, deterministic) in `ndcentral.log` + `power_mon.log`:

Tuple: `PROCESS=NDC, CODE=30023, CODE_AUX=3, DESCRIPTION="Camera LPM crash"`

Confirmed sequence immediately preceding every 30023 emission:

1. `power_mon.log`: `Device has booted up` (or `#####STARTING POWER MONITOR#####`) — the whole device/process just (re)started.
2. `ndcentral.log`: `DEVB3` logs a `Reset Wake Reason`/`WAKEUP_REASON` boot marker at essentially the same timestamp.
3. `IGNITION ON received` or `IGNITION OFF received` fires a few seconds later (direction is inconsistent across occurrences — both are seen).
4. `MAKE_ERROR_CALLBACK message received` — camera error callback fires, ~17-22s after the boot marker in step 1.
5. `cam_crash_status.status[<i>] = 1` for the affected camera index, all others `0`.
6. `cam_num <i> is crashed` — NDC confirms the specific camera as crashed.
7. `DEVB3` re-checks camera boot status over I2C (e.g. `Right camera command_1/2 : i2cget -f -y 7 0x3d 0x02/0x03`, then `get_right_cam_boot_status_tt return status 0`) — the "retry ISP status" check.
8. `Camera LPM crash for cam_num <i>` fires — the `send_err_msg(SM_E_NDC_CAM_LPM_CRASH, ...)` call.
9. `set_property CAM<i>_CRASH_COUNT with <n>` — crash counter incremented for that camera.

No LPM/suspend/resume/camera-specific wake marker appears anywhere in this window on any confirmed occurrence — ruling out a mid-session power-saving sleep/wake cycle as the trigger. Confirmed on three independent devices: `103452403664` (5 occurrences, camera index 3), `103452403525` (1 occurrence, camera index 3), and `103062502288` (3 occurrences, camera indexes 2 and 3) — all 9 occurrences show the same ~17-22s-post-boot timing.

**Confidence:** `High` that this is the correct emitter/code path — steps 4-9 are identical across all 9 occurrences on 3 devices. `Medium` on the power-management mechanism implied by the event name — no log evidence confirms an actual camera LPM sleep/wake transition; the reliably correlated factor is device reboot + ignition transition, not a power-saving cycle.
