---
name: btfv-critical-errors
description: "Use when analyzing BTFV/nd_bt critical events, specifically CODE=10011 (BT module enable failed)."
---

# BTFV Critical Errors

Apply the shared narrowing method and confidence rubric in `critical-event-query-triage/SKILL.md` to every tuple below.

## `SM_E_BTFV_BT_STACK_FAIL` — enable-failed subtype (CODE=10011)

**Identify:** PROCESS=`nd_bt_man`/`BTFV`, DESCRIPTION=`BT module enable failed`, `CODE_AUX`=`1` maps to `BtStackErr::kEnableFailed`.

**In plain terms:** The device tried twice to turn on its Bluetooth radio and both attempts failed, so it gave up and logged this event. There are two different ways the attempt fails on the ground — either the enable command itself errors out immediately, or it just hangs and times out — but either way the outcome (BT stays off after two tries) is the same.

**Why it triggered:** The BT manager tries `bt_interface_ptr_->Enable()` up to two times, disabling in between failures, and only emits this event if BT is still disabled after both retries. Source: `nd_bt/src/daemon/nd_bt_man.cpp`, `send_err_msg(SM_E_BTFV_BT_STACK_FAIL, static_cast<int>(BtStackErr::kEnableFailed), "BT module enable failed")`. Checked against raw logs on 2 devices (2075+ total occurrences), the "exactly two attempts then cleanup" skeleton is fully deterministic, but the proximate failure signature at each attempt is NOT universal — it splits into two distinct patterns depending on device/instance:
- **Exit-failure pattern:** `bluetoothctl` power-on exits with status `1` immediately (dominant on device `3633038377`: ~10,255 of its occurrences).
- **Timeout pattern:** the enable command times out (`UTIL: E: Timeout occurred`), and `bluetoothctl` itself exits `0` but logs a bluez D-Bus error (`Failed to set power on: org.bluez.Error.Failed` or `org.bluez.Error.Blocked`) — dominant on device `3633101236` (64 timeouts vs. only 4 exit-status-1 failures, and those 4 isolated ones recovered on retry without ever reaching a 10011).

**Evidence** (log-validated, cross-device, deterministic on the retry skeleton) in `btfv.log`:

Tuple: `PROCESS=nd_bt_man, CODE=10011, CODE_AUX=1, DESCRIPTION="BT module enable failed"`

1. `rfkill` unblock runs and exits 0 (software BT block is off).
2. `bluetoothctl` power-on command executed — fails via **either** exit status `1` **or** a command timeout (`Timeout occurred`) followed by an `org.bluez.Error.*` message with exit status `0`.
3. `HCI ... ERROR: Execute failed for command: bluetoothctl` logged, then `HCI ... BT enable failed`.
4. Disable cycle runs (`bluetoothctl` disable).
5. Steps 2-4 repeat once more (second and final retry) — always exactly 2 failed attempts, never more or fewer, across all 2075+ sampled occurrences on both devices.
6. After the second failure: `BTMAN ... Cleanup and exit service as BT module enable failed`.
7. `service_mon` emits `10011 : 1 : BT module enable failed`.

Confirmed on devices `3633038377` (**2051-2173 occurrences, not a single occurrence** — cycling roughly every 15 seconds for ~23 hours straight; `No default controller available` appears exactly once at the very first BTFV startup in the log and is not tied to the 10011 event generally, just an incidental one-time line) and `3633101236` (24 occurrences, "dozens" as originally described, all following the same 2-attempt-then-cleanup skeleton but with the timeout signature dominant).

**Confidence:** `High` on the retry skeleton (2 failed attempts → cleanup → 10011) — verified deterministic across 2075+ occurrences on 2 devices. `Medium` on any specific single-line proximate cause (exit-status-1 vs. timeout+bluez-error) — which one applies is device/instance-specific, so check the actual attempt lines rather than assuming either pattern.
