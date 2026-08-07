---
name: bootup-periodic-gpio-usb-hub-health-check
description: "Use when: validating the bootup periodic health-monitor path for haptic_feedback. Covers initial GPIO state checks, USB hub detection, and absence of GPIO read errors after boot."
argument-hint: "device ID (e.g., /haptic-bootup-periodic-gpio-usb-hub-health-check 440073)"
---

# Haptic — Flow 4: Bootup Periodic GPIO/USB Hub Health Check

## What happens

On every boot, `periodic_health_monitor` performs its first health check independent of
ignition state. The service should log the GPIO output and input state, confirm the USB
hub is detected, and avoid GPIO read failures. The exact USB hub log string differs by
device type.

**When active:** `[haptic_feedback] enabled=1`
**Frequency:** Once per boot, then periodically afterward
**Cross-service impact:** None beyond local hardware monitoring
**Automated:** Yes
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** No

## Key evidence

### haptic_feedback.log

- `Periodic health: output_state=0, input_gpio=1`
- `USB hub detected` on `bagheera3`
- `USB hub device 1 connected` on `octo`
- No `Periodic health: failed to read input GPIO` errors

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4162` | `tests/haptic/test_tc_haptic_4162_haptic_check_upon_bootup.py` | bootup periodic health check, USB hub detection, and clean GPIO reads |

## Pass criteria

- The periodic health status line appears after boot
- The correct device-type-specific USB hub detection line appears
- No GPIO read failure is logged for the bootup check

## Fail signals

- The periodic health check does not appear after boot
- The expected USB hub detection line is missing
- A GPIO read failure is logged during the bootup health check

## Validation instructions

1. Confirm device type is `bagheera3` or `octo`
2. Verify the periodic health status line appears after boot
3. Match the USB hub detection string to the actual device type
4. Confirm no GPIO read failure is logged for the bootup check
5. Keep this flow separate from the ignition-on audio health-check path