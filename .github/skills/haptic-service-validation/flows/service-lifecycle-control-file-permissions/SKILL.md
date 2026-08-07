---
name: service-lifecycle-control-file-permissions
description: "Use when: validating haptic_feedback systemd lifecycle control and installed file permissions. Covers stop, restart, enable, disable, and expected permission modes for scripts, binary, and service unit."
argument-hint: "device ID (e.g., /haptic-service-lifecycle-control-file-permissions 440073)"
---

# Haptic — Flow 2: Service Lifecycle Control & File Permissions

## What happens

`haptic_feedback` must support normal systemd lifecycle operations. The service should
transition correctly for `stop` and `restart`, and `systemctl is-enabled` should reflect
enable and disable operations. Disabling the unit must not stop an already running
process. The installed artifacts must also have the expected permissions: shell scripts
and the binary should be executable, while the `.service` unit should be readable but
not executable.

**When active:** On-demand lifecycle validation
**Frequency:** Per manual or automated lifecycle test run
**Cross-service impact:** None beyond `systemctl` state checks
**Automated:** Yes
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** No

## Expected behavior

- `systemctl stop` leads to `inactive`
- `systemctl restart` leads back to `active`
- `systemctl disable` changes enablement state but does not kill a running process
- `systemctl enable` restores enablement state
- Installed `.sh` files and binary use `-rwxr-xr-x`
- Installed `.service` file uses `-rw-r--r--`

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4345` | `tests/haptic/test_tc_haptic_4345_service_lifecycle_and_permissions.py` | stop/restart/enable/disable behavior and file permissions |

## Pass criteria

- `stop` leads to `inactive`
- `restart` leads back to `active`
- `disable` changes enablement state without killing a running process
- `enable` restores enablement state
- Installed files match the expected permission modes

## Fail signals

- Any lifecycle transition produces the wrong `systemctl` state
- Disabling the unit stops the running process unexpectedly
- Script, binary, or service-unit permissions differ from the expected modes

## Validation instructions

1. Confirm device type is `bagheera3` or `octo`
2. Verify `systemctl is-active` transitions match the expected stop and restart states
3. Verify `systemctl is-enabled` reflects disable and enable operations
4. Confirm disable does not stop an already running `haptic_feedback` process
5. Compare file permissions against the expected modes from the existing test