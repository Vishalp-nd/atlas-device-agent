---
name: adb-shell-reboot
description: 'Reboot ADB-connected devices using an in-shell reboot command instead of external adb reboot. Use when a test case requires device reboot, pre_steps contain action:reboot, or the agent needs to restart the device during sanity validation.'
---

# ADB Shell Reboot

Reboot the target device by executing `reboot` **inside** the ADB shell session rather than using the external `adb reboot` host command.

## Why In-Shell Reboot

- `adb reboot` sends a reboot request from the host via the ADB protocol — it can silently fail on devices where the ADB daemon is unresponsive or partially connected.
- `adb shell reboot` (i.e., `run_adb_command("reboot")`) executes the Linux `reboot` syscall directly on the device OS, which is more reliable for embedded Bagheera/Krait boards.
- The `run_adb_command` tool already wraps commands inside `adb -s <serial> shell`, so passing `reboot` achieves `adb shell reboot` automatically.

## When to Use

- A test case contains `pre_steps` with `action: reboot`.
- The acceptance criteria require verifying behaviour after a fresh boot.
- The agent decides a reboot is needed to reset device state mid-test.

## Procedure

1. **Run the reboot command inside the device shell:**
   ```
   run_adb_command("reboot")
   ```
   This executes `adb -s <serial> shell reboot` on the device.

2. **Poll for device readiness** immediately after the reboot command returns (or times out). Retry a simple connectivity check until the device responds:
   ```
   run_adb_command("uptime")
   ```
   The device is back when this succeeds with a low uptime value (close to 0 minutes).

3. **Verify the device freshly booted** before continuing test execution:
   ```
   run_adb_command("uptime")
   ```
   Confirm uptime has reset to near zero — this proves the reboot actually happened.

## Rules

- **Never** attempt to run `adb reboot` as a shell command — it does not exist on-device and will fail.
- Always use `run_adb_command("reboot")` which routes through `adb shell`.
- If the reboot command returns a non-zero exit code or times out, that is **expected** — the device is shutting down and the ADB connection drops.
- After reboot, poll immediately — do **not** wait a fixed duration. Retry `uptime` until the device responds.
- The device has successfully rebooted when `uptime` returns a value near zero.
- If `uptime` keeps failing after multiple retries, report the test as FAIL with reason "Device did not come back after reboot".
