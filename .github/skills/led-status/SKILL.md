---
name: led-status
description: "Use when: checking LED color/brightness on device (left/right LED for red/green/blue), checking IR LED brightness, or verifying LED state changes after config/privacy/reboot events. Ported from nd_test_bot (6.14_changes branch) led_api.py. Works over serial connection via serial_conn.py."
argument-hint: "action (e.g., get_led_status left red, get_led_status right blue, get_irled_status)"
---

# LED Status

Read LED brightness values from device to verify LED color state. Supports left/right visible LEDs (red, green, blue) and IR LED. Platform-aware: uses correct sysfs paths per device type.

**Source**: Ported from `nd_test_bot` repo, branch `6.14_changes`, file `Test_Automation_Framework/Lib/apis/led_api.py`.

## When to Use

- **get_led_status**: Check if a specific LED (left/right) is ON or OFF for a given color (red/green/blue)
- **get_irled_status**: Check IR LED brightness level
- Verify LED color after privacy mode changes, ignition events, off-duty transitions, config changes
- Verify LED turns purple (red ON + blue ON, green OFF) for enhanced privacy

## Prerequisites

- Device connected via serial (`/dev/ttyACM0`) for D450 or ADB for D210
- `serial_conn.py` available at `claude_device_validator/src/serial_conn.py`

## LED Mapping by Platform

### bagheera3 / D450 (current device)

Visible LEDs read from sysfs brightness files:

| LED | Color | Path |
|-----|-------|------|
| Left | Red | `/sys/class/leds/bag_left_red/brightness` |
| Left | Green | `/sys/class/leds/bag_left_green/brightness` |
| Left | Blue | `/sys/class/leds/bag_left_blue/brightness` |
| Right | Red | `/sys/class/leds/bag_right_red/brightness` |
| Right | Green | `/sys/class/leds/bag_right_green/brightness` |
| Right | Blue | `/sys/class/leds/bag_right_blue/brightness` |

- Brightness value `> 0` means LED is **ON**
- Brightness value `== 0` means LED is **OFF**

IR LED: Uses `irled_status_b3.sh` script (checks IR LED controller register).

### bagheera2

Uses GPIO pins:

| LED | Color | GPIO |
|-----|-------|------|
| Left | Red | 249 |
| Left | Green | 248 |
| Left | Blue | 250 |
| Right | Red | 252 |
| Right | Green | 251 |
| Right | Blue | 253 |

Command: `gpio_test -n <GPIO_NUM>` → parse `value:` field. Value `1` = OFF, `0` = ON (inverted logic).

### krait / D210

| LED | Color | Path |
|-----|-------|------|
| Left | * | `/sys/devices/platform/soc/soc:vvdn-dcam/leds/dcam_left_<color>/brightness` |
| Right | * | `/sys/devices/platform/soc/soc:vvdn-dcam/leds/dcam_right_<color>/brightness` |
| Right | Blue | `/sys/devices/platform/soc/c440000.qcom,spmi/spmi-0/spmi0-03/c440000.qcom,spmi:qcom,pm660l@3:qcom,leds@d000/leds/dcam_right_blue/brightness` |

### krait2

| LED | Color | Path |
|-----|-------|------|
| Left | * | `/sys/devices/platform/soc/soc:vvdn-clik/leds/clik_left_<color>/brightness` |
| Right | * | `/sys/devices/platform/soc/soc:vvdn-clik/leds/clik_right_<color>/brightness` |

### octo

Uses I2C register: `i2c_transfer 0 8 2 74 7 | awk '{print $NF}' | tr -d '.'`

Register bitmask:

| LED | Color | Bit |
|-----|-------|-----|
| Left | Blue | 0x02 |
| Left | Green | 0x04 |
| Left | Red | 0x08 |
| Right | Blue | 0x20 |
| Right | Green | 0x40 |
| Right | Red | 0x80 |

## Procedures

### get_led_status — Check visible LED color

Method varies by platform:

**bagheera3** (D450) — sysfs brightness files:
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "cat /sys/class/leds/bag_<SIDE>_<COLOR>/brightness"
```

**krait** (D210) — sysfs brightness files:
```bash
# Most LEDs:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "cat /sys/devices/platform/soc/soc:vvdn-dcam/leds/dcam_<SIDE>_<COLOR>/brightness"
# Special case — right blue only:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "cat /sys/devices/platform/soc/c440000.qcom,spmi/spmi-0/spmi0-03/c440000.qcom,spmi:qcom,pm660l@3:qcom,leds@d000/leds/dcam_right_blue/brightness"
```

**krait2** — sysfs brightness files:
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "cat /sys/devices/platform/soc/soc\:vvdn-clik/leds/clik_<SIDE>_<COLOR>/brightness"
```

**bagheera2** — GPIO (inverted logic: value 1 = OFF, 0 = ON):
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "gpio_test -n <GPIO_NUM>"
# Parse "value: X" from output. GPIO map: left_red=249, left_green=248, left_blue=250, right_red=252, right_green=251, right_blue=253
```

**octo** — I2C register with bitmask:
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "i2c_transfer 0 8 2 74 7 | awk '{print \$NF}' | tr -d '.'"
# Result is hex. Bitmask: left_blue=0x02, left_green=0x04, left_red=0x08, right_blue=0x20, right_green=0x40, right_red=0x80
```

Where:
- `<SIDE>`: `left` or `right`
- `<COLOR>`: `red`, `green`, or `blue`

**Polling**: Poll up to 5 times with 2s interval if needed to confirm stable state.

**Return**: brightness value (integer). `> 0` = ON, `0` = OFF.

**Examples** (bagheera3):
```bash
python3 claude_device_validator/src/led_status.py --device-id 103382300371 --via-serial get_led_status --led right --color red
python3 claude_device_validator/src/led_status.py --device-id 103382300371 --via-serial get_led_status --led left --color green
python3 claude_device_validator/src/led_status.py --device-id 103382300371 --via-serial get_led_status --led right --color blue
```

**Examples** (krait via adb):
```bash
python3 claude_device_validator/src/led_status.py --device-id <DEVICE_ID> --via-adb --platform krait get_led_status --led right --color red
```

**Examples** (octo via serial):
```bash
python3 claude_device_validator/src/led_status.py --device-id <DEVICE_ID> --via-serial --platform octo get_led_status --led left --color blue
```

### get_irled_status — Check IR LED brightness

Method varies by platform:

**krait / krait2** — sysfs:
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "cat /sys/class/leds/ir_led/brightness"
```

**bagheera3 / bagheera2 / octo** — via `cli_mgr`:
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "echo 'irled status' | sudo cli_mgr 2>/dev/null | grep -i 'brightness level'"
```

Output: `Current status is with brightness level <N>` — parse the integer.

CLI prompts by platform: `B3_V2 >` (bagheera3), `NTDI_BAG2 >` (bagheera2), `NTDI_OCTO >` (octo).

**Return**: brightness level integer.

### Verify LED Color Combinations

Common color verifications:

| Expected Color | Checks |
|----------------|--------|
| **Green** | right green > 0, right red == 0, right blue == 0 |
| **Red** | right red > 0, right green == 0, right blue == 0 |
| **Blue** | right blue > 0, right red == 0, right green == 0 |
| **Purple** | right red > 0, right blue > 0, right green == 0 |
| **OFF** | all == 0 |

## CLI Usage

```bash
python3 claude_device_validator/src/led_status.py --device-id <DEVICE_ID> --via-serial get_led_status --led <left|right> --color <red|green|blue>
python3 claude_device_validator/src/led_status.py --device-id <DEVICE_ID> --via-serial get_irled_status

# Examples:
python3 claude_device_validator/src/led_status.py --device-id 103382300371 --via-serial get_led_status --led right --color red
python3 claude_device_validator/src/led_status.py --device-id 103382300371 --via-serial get_led_status --led left --color green
python3 claude_device_validator/src/led_status.py --device-id 103382300371 --via-serial get_irled_status
```

## Important Notes

- LED brightness files are in `/sys/class/leds/` — these are real-time hardware values
- For bagheera3: file naming is `bag_<side>_<color>` (e.g., `bag_right_red`)
- For krait: file naming is `dcam_<side>_<color>` (e.g., `dcam_left_green`)
- For krait2: file naming is `clik_<side>_<color>` (e.g., `clik_right_blue`)
- Poll 5 times with 2s delay when TC says "poll 5x" — LED may take time to change after event
- Purple = red ON + blue ON (red brightness > 0 AND blue brightness > 0)
- Inverted logic on bagheera2 GPIO: value 1 = OFF, value 0 = ON
