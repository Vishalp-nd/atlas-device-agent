---
name: d470-hardware-validation
description: "Use when: running Hardware+OS validation test cases on a D470 (bagheera4) device via cli_mgr. Covers GPIO, temperature, fan, LED, ignition, IMU, IR LED, supercap, ADC, button, audio, LTE/GPS, DMS, and other hardware subsystem checks. Provides cli_mgr execution patterns, command reference, and expected response strings."
argument-hint: "device serial (e.g., /d470-hardware-validation 2543fa04)"
---

# D470 Hardware + OS Validation — cli_mgr Test Reference

This skill enables the **serial-testcase-executor** agent to run hardware validation test cases on a **D470** (`bagheera4`) device using the proprietary `cli_mgr` interactive shell.

## What is cli_mgr?

`cli_mgr` is an interactive command-line interface on D470 devices for direct hardware subsystem control. It provides low-level access to GPIO, fans, temperature sensors, LEDs, ignition, IMU, IR LEDs, supercaps, ADC, buttons, audio, LTE/GPS, DMS, and other hardware peripherals.

- **Binary location:** `/usr/bin/cli_mgr`
- **Prompt:** `NTDI_B4 >` (or just `>`)
- **Entry:** run `cli_mgr` in an ADB shell
- **Exit:** send `exit` command inside cli_mgr

> **cli_mgr is NOT the same as ADB shell.** ADB shell runs Linux commands; cli_mgr runs proprietary hardware API commands.

---

## Executing cli_mgr Commands via ADB

### Single Command

```bash
adb -s <SERIAL> shell 'printf "<COMMAND>\nexit\n" | cli_mgr 2>&1'
```

Example:
```bash
adb -s 2543fa04 shell 'printf "gpio get 0\nexit\n" | cli_mgr 2>&1'
```

### Multiple Commands in One Session

```bash
adb -s <SERIAL> shell 'printf "<CMD1>\n<CMD2>\n<CMD3>\nexit\n" | cli_mgr 2>&1'
```

Example:
```bash
adb -s 2543fa04 shell 'printf "temp init\ntemp get_temp 12\ntemp uninit\nexit\n" | cli_mgr 2>&1'
```

### Commands That Require Init/Uninit

Many hardware subsystems require initialization before use and cleanup after:

```bash
adb -s <SERIAL> shell 'printf "<subsystem> init\n<subsystem> <action>\n<subsystem> uninit\nexit\n" | cli_mgr 2>&1'
```

### Important Notes

- **Always append `exit` as the last command** — otherwise cli_mgr hangs waiting for input.
- **Use `printf` with `\n`** — do NOT use `echo` (it sends all text as one line).
- **Pipe output through `2>&1`** — cli_mgr writes some output to stderr.
- **Output includes the prompt and command echo** — e.g., `NTDI_B4 > gpio get 0` appears before the result.
- **Some commands produce `[INFO]` log lines mixed with results** — parse the structured output lines, not the `[INFO]` debug lines.

---

## cli_mgr Command Reference

### GPIO Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `gpio get <pin>` | `gpio number = <pin>, value = <0\|1>` | Valid pins: 0, 1, 50, 100, 132. Pin 133 and -1 are negative tests |
| `gpio get 133` | Error response (NOT `gpio number = 133, value =`) | Negative test — should fail |
| `gpio get -1` | Error response | Negative test — should fail |

### Temperature Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `temp init` | `TEMP initialization successful` | Must call before get_temp |
| `temp get_temp 12` | Contains `Temperature` and `C` (Celsius) | e.g., `CPUSS1 Temperature (max temp): 50 C` |
| `temp uninit` | `TEMP uninitialization successful` | Cleanup |

### Fan Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `fan init` | `FAN initialization successful` | Must call first |
| `fan on 1` | `fan_setup: Fan set to manual mode: 1` AND `fan_setup: Fan turned ON (full speed)` | Manual mode |
| `fan on 2` | `fan_setup: Fan set to auto mode: 2` AND `FAN: Turned ON` | Auto mode |
| `fan off` | `FAN: Turned OFF` | |
| `fan get_status` | Contains `Fan Status:`, `PWM`, `RPM` | When on: `PWM = 255`; when off: `PWM = 0`, `RPM = 0` |
| `fan uninit` | `FAN uninitialization successful` | Cleanup |

### LED Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `led clear <led_id> <color>` | (no error) | led_id: 1 or 2; color: R, G, B |
| `led intensity_set <led_id> <color> <intensity> <max>` | (no error) | e.g., `led intensity_set 1 R 100 255` |

### Ignition Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `ignition status` | `Ignition state: ON` or `Ignition state: OFF` | Read current state |
| `ignition register` | `Ignition callback registration successful` | Register for state-change alerts |
| `ignition unregister` | `Ignition callback unregistration successful` | Unregister |

### IMU Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `imu init` | IMU initialization message | |
| `imu get_data` | Accelerometer/gyroscope data | |
| `imu get_fru` | FRU info output | |
| `imu uninit` | IMU uninit message | |

### IR LED Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `irled init` | IR LED initialization | |
| `irled on` | IR LED turned on | |
| `irled off` | IR LED turned off | |
| `irled uninit` | IR LED uninitialization | |

### Supercap Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `supercap init` | Supercap init message | |
| `supercap get_status` | Supercap status with voltage/charge info | |
| `supercap uninit` | Supercap uninit message | |
| `ltc3350 init` | LTC3350 init | |
| `ltc3350 get_status` | LTC3350 charge controller status | |
| `ltc3350 uninit` | LTC3350 uninit | |

### ADC Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `adc init` | ADC initialization | |
| `adc get_value <channel>` | ADC value for channel | |
| `adc uninit` | ADC uninitialization | |

### Button Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `button init` | Button initialization | |
| `button get_status` | Button state | |
| `button uninit` | Button uninitialization | |

### Audio Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `audio init` | Audio initialization | |
| `audio play <file>` | Audio playback | |
| `audio uninit` | Audio uninitialization | |

### LTE/GPS Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `lte_gps init` | LTE/GPS initialization | |
| `lte_gps get_status` | Signal/connection status | |
| `lte_gps uninit` | LTE/GPS uninitialization | |

### DMS Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `dms init` | DMS initialization | |
| `dms get_status` | DMS status | |
| `dms uninit` | DMS uninitialization | |

### AON (Always-On) Commands

| Command | Expected Success Output | Notes |
|---------|------------------------|-------|
| `aon init` | AON initialization | |
| `aon get_status` | AON status | |
| `aon uninit` | AON uninitialization | |

---

## Complete Factory Test Case Inventory (D470-OS)

| TC ID | Test Name | cli_mgr? | Subsystem | Automatable? |
|-------|-----------|----------|-----------|-------------|
| TC_3664 | STORAGE_CHECK | No | Storage | Yes |
| TC_3665 | WIFI_CHECK | No | WiFi | Yes |
| TC_3666 | IMU_CHECK | Yes | imu | Yes |
| TC_3667 | IMU_FRU_CHECK | Yes | imu | Yes |
| TC_3668 | IRLED_CHECK | Yes | irled | Yes |
| TC_3686 | LUMIA3_CHECK | No | Lumia3 | Partial |
| TC_3687 | SSD_CHECK | No | SSD | Yes |
| TC_3688 | GPS_CHECK | Yes | lte_gps | Yes |
| TC_3689 | RTC_CHECK | No | RTC | Yes |
| TC_3690 | FIRMWARE_CHECKS | No | Firmware | Yes |
| TC_3691 | WOM_CHECK | No | WOM | Yes |
| TC_3765 | BT_CHECK | No | Bluetooth | Yes |
| TC_3766 | CAMERA_LED_CHECK | Yes | led | No (visual) |
| TC_3767 | PHOTO_TRANSISTOR_CHECK | Yes | gpio/adc | Partial |
| TC_3768 | BUTTON_CHECK | Yes | button | Partial (physical) |
| TC_3770 | MIC_AND_SPEAKER_CHECK | Yes | audio | No (auditory) |
| TC_3771 | VERSION_CHECK | No | System | Yes |
| TC_3772 | IGNITION_STATE_CHECK | Yes | ignition | Yes (with relay) |
| TC_3773 | TEMPERATURE_CHECK | Yes | temp | Yes |
| TC_3774 | GPIO_CHECK | Yes | gpio | Yes |
| TC_3775 | FAN_CHECK | Yes | fan | Yes |
| TC_3776 | LTE_SIM_DETECTION_CHECK | Yes | lte_gps | Yes |
| TC_3786 | SUPERCAP_CHECK | Yes | supercap | Yes |
| TC_3788 | DMS_CONNECTOR_CABLE_CHECK | Yes | dms | Partial |
| TC_3789 | ADC_CHECK | Yes | adc | Yes |
| TC_3796 | FRU_INFO_CHECK | No | FRU | Yes |
| TC_3797 | AON_CHECK | Yes | aon | Yes |
| TC_3798 | DMS_CHECK | Yes | dms | Yes |
| TC_3872 | I2C_CHECK | No | I2C | Yes |
| TC_3873 | CAMERA_STREAM_CHECK | No | Camera | Partial |

---

## Troubleshooting

### cli_mgr hangs / no output
- **Cause:** Missing `exit` in the printf command sequence
- **Fix:** Always end with `\nexit\n`

### Garbled output / "Too many characters" error
- **Cause:** Using `echo` instead of `printf`, or piping too much data
- **Fix:** Use `printf` with explicit `\n` separators

### "command not found"
- **Cause:** cli_mgr not in PATH or not installed on this device variant
- **Fix:** Check `adb -s <SERIAL> shell "which cli_mgr"` — should return `/usr/bin/cli_mgr`

### Init fails
- **Cause:** Subsystem already initialized by another process, or hardware not present
- **Fix:** Try uninit first, then init again

### Timeout during fan/temp operations
- **Cause:** Hardware operation takes longer than default timeout
- **Fix:** These commands typically complete within 1-2 seconds via ADB pipe; no special timeout handling needed
