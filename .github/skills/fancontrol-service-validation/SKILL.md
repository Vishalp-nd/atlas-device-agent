---
name: fancontrol-service-validation
description: "Use when: running fan_control service validation test cases on Netradyne devices. fan_control is a krait/krait2-only service — all tests skip with NA on bagheera/octo. Covers config parsing (enable/disable, all default threshold and PWM values), service status, enable/disable via config push, temperature+PWM logging loop at configured interval, log file storage location, and low-power wakeup (LPW) PWM=0 behavior."
argument-hint: "device serial (e.g., /fancontrol-service-validation 103452403525)"
---

# fan_control — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the fan_control
> service — what it does, how its flows relate to each other, and which config keys
> activate which flows. Derived from source code at
> `nd_device_services/fan_control/src/fan_control.cpp` (no live log file available).

---

## Service Overview

`fan_control` is a krait/krait2-only system service that reads the CPU die temperature every `interval` seconds from the sysfs node `/sys/class/hwmon/hwmon1/../../die_temp` and writes a PWM duty cycle value to `/sys/class/leds/fan_control/brightness` to control the physical fan. It implements a 5-state hysteresis state machine: temperature rising through `up_thr[N]` increments the fan state (higher PWM), temperature falling below `down_thr[N]` decrements it. On every cycle it logs a single line: `temperature = <N>, fan PWM = <N>`. If `fan_enable = false`, the service logs "Feature Disabled, Exiting" and calls `stop_service("fan_control.service")` immediately after config parse.

**Process name:** `fan_control`
**Log folder:**
- krait / krait2: `/data/nd_files/log/fan/`
- (source code default for bagheera: `/home/ubuntu/.nddevice/log/fan/` — not used in tests)

**Log TAG:** `FAN`
**Config file:** `/home/ubuntu/.nddevice/latest/bagheera_config.ini` (+ `bagheera_override.ini` for overrides)
**Primary config section:** `[fan]`
**Sysfs nodes:**
- CPU temp: `/sys/class/hwmon/hwmon1/../../die_temp`
- Fan PWM: `/sys/class/leds/fan_control/brightness`

**Device restriction:** krait / krait2 **only**. All test cases exit NA on any other device type.

---

## Log Format

```
<epoch_ms>: <uptime_ms>: FAN: <level>: <pid>: <tid>: <message>
```

No live device log is available for this service. All log patterns below are derived directly from source code (`fan_control.cpp`).

---

## Default Config Values

These are the values asserted by TC_fancontrol_612 and match the compiled-in defaults from source code. The config file sets `interval=2`; the source code compiled default is 5.

| Config Key | Section | Default Value | Description |
|---|---|---|---|
| `enable` | `[fan]` | `true` | Enable/disable fan control |
| `interval` | `[fan]` | `2` (config) / `5` (compiled) | Polling interval in seconds |
| `fan_pwm0` | `[fan]` | `0` | PWM for state 0 (coolest) |
| `fan_pwm1` | `[fan]` | `80` | PWM for state 1 |
| `fan_pwm2` | `[fan]` | `120` | PWM for state 2 |
| `fan_pwm3` | `[fan]` | `160` | PWM for state 3 |
| `fan_pwm4` | `[fan]` | `255` | PWM for state 4 (max) |
| `up_thr1` | `[fan]` | `51` | Rising threshold: state 0 → 1 |
| `up_thr2` | `[fan]` | `61` | Rising threshold: state 1 → 2 |
| `up_thr3` | `[fan]` | `71` | Rising threshold: state 2 → 3 |
| `up_thr4` | `[fan]` | `82` | Rising threshold: state 3 → 4 |
| `down_thr1` | `[fan]` | `36` | Falling threshold: state 1 → 0 |
| `down_thr2` | `[fan]` | `52` | Falling threshold: state 2 → 1 |
| `down_thr3` | `[fan]` | `62` | Falling threshold: state 3 → 2 |
| `down_thr4` | `[fan]` | `72` | Falling threshold: state 4 → 3 |

---

## Service Flows

### Flow 1: Config Parsing & Initialization

**What happens:** At startup, `fan_control` reads `bagheera_config.ini` (with override applied from `bagheera_override.ini`) via `Config_parser`. It first reads `fan.enable`. If enabled, it reads all threshold and PWM values and logs them. Then it opens the fan sysfs file. If disabled, it logs "Feature Disabled, Exiting" and stops the systemd unit immediately without entering the main loop.

**When active:** Every service start / restart
**Frequency:** Once at boot or restart
**Cross-service impact:** None — fan_control does not depend on or send messages to other services

**Key log patterns (enabled path — from source):**
```
FAN: I: <pid>: <pid>: Reading config file...
FAN: I: <pid>: <pid>: fan_enable = true
FAN: I: <pid>: <pid>: Up thresholds: 51, 61, 71, 82
FAN: I: <pid>: <pid>: Down thresholds: 36, 52, 62, 72
FAN: I: <pid>: <pid>: Fan PWM: 80, 120, 160, 255
```

**Key log patterns (disabled path — from source):**
```
FAN: I: <pid>: <pid>: Reading config file...
FAN: I: <pid>: <pid>: fan_enable = false
FAN: I: <pid>: <pid>: Feature Disabled, Exiting
FAN: I: <pid>: <pid>:  Fan Control Service Is Stopped Successfully
```

**Key log patterns (sysfs open failure — from source):**
```
FAN: E: <pid>: <pid>: FAN:File not opened
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_fancontrol_612` | `tests/fancontrol/test_tc_fancontrol_612_default_config.py` | All 15 config keys read from `bagheera_config.ini` match exact default values (enable=true, interval=2, fan_pwm0=0, fan_pwm1=80, fan_pwm2=120, fan_pwm3=160, fan_pwm4=255, up_thr1=51, up_thr2=61, up_thr3=71, up_thr4=82, down_thr1=36, down_thr2=52, down_thr3=62, down_thr4=72) | — |
| `TC_fancontrol_569` | `tests/fancontrol/test_tc_fancontrol_569_config_enabled_validation.py` | `fan.enable == "true"` in `bagheera_config.ini` AND `fan_control` service `is_active == True` | — |
| `TC_fancontrol_571` | `tests/fancontrol/test_tc_fancontrol_571_config_disabled_validation.py` | Push `fan.enable=false` to `bagheera_override.ini`, upload, reboot → `fan_control` service `is_active == False`; restore `fan.enable=true` after | — |

---

### Flow 2: Service Status & Enable/Disable via Config Push

**What happens:** `fan_control` is managed as a systemd unit. When `fan.enable=true` (default), the service starts and remains active. Pushing `fan.enable=false` to the override config and restarting (or rebooting) causes the service process to exit immediately after reading the disabled flag, making it inactive. Pushing `fan.enable=true` and restarting restores normal operation. TC_617 and TC_618 use `restart_service` (not reboot) to test the enable/disable toggle; TC_571 uses a full reboot.

**When active:** Always — service lifecycle management
**Frequency:** Per restart or config push
**Cross-service impact:** None

**Verification commands:**
```bash
# Check service status
systemctl is-active fan_control

# Start manually if inactive
sudo systemctl start fan_control
```

**Config push sequence (disable):**
```ini
[fan]
enable = false
```
Then upload override and `restart_service(["fan_control"])`.

**Config push sequence (enable):**
```ini
[fan]
enable = true
```
Then upload override and `restart_service(["fan_control"])`.

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_fancontrol_615` | `tests/fancontrol/test_tc_fancontrol_615_service_status.py` | `fan_control` service `is_active == True`; starts it via `systemctl start fan_control` if not already active | — |
| `TC_fancontrol_617` | `tests/fancontrol/test_tc_fancontrol_617_enable_by_config_push.py` | Push `fan.enable=true` to override, upload, `restart_service` → `is_active == True` | — |
| `TC_fancontrol_618` | `tests/fancontrol/test_tc_fancontrol_618_disable_by_config_push.py` | Push `fan.enable=false`, upload, `restart_service` → `is_active == False`; restore `fan.enable=true` after | — |

---

### Flow 3: Temperature + PWM Logging Loop

**What happens:** When enabled, `fan_control` enters an infinite loop. Each iteration: reads CPU die temperature from `/sys/class/hwmon/hwmon1/../../die_temp`, evaluates the hysteresis state machine to determine the new fan state, writes the PWM value to `/sys/class/leds/fan_control/brightness`, then logs a single line: `temperature = <N>, fan PWM = <N>`. After logging it sleeps for `interval` seconds (default 2). Tests extract the last 10 timestamps from these log lines and use `interval_check` to verify they are spaced at the configured interval.

**When active:** Always when `fan_enable=true`, continuously
**Frequency:** Every `interval` seconds (default 2s in config)
**Cross-service impact:** None — writes directly to sysfs

**Key log pattern (from source — main loop, every `interval` seconds):**
```
FAN: I: <pid>: <pid>: temperature = <N>, fan PWM = <N>
```

Examples of expected output (actual values depend on current device temperature):
```
FAN: I: 1234: 1234: temperature = 48, fan PWM = 0
FAN: I: 1234: 1234: temperature = 53, fan PWM = 80
FAN: I: 1234: 1234: temperature = 63, fan PWM = 120
```

**Shell command to extract timestamps for interval check (krait):**
```bash
grep -ir 'temperature =' /data/nd_files/log/fan/*.log | tail -10 | awk -F':' '{gsub(" ", "", $2); print $2}'
```
Returns the 10 most recent uptime_ms values from log lines; fed to `interval_check` to verify ~2000ms spacing.

**Shell command to extract last PWM value:**
```bash
grep -ir 'fan PWM' /data/nd_files/log/fan/*.log | tail -n 1 | awk -F' = ' '{print $NF}'
```

**Fan state machine (from source):**
| State | PWM | Enters when temp > | Exits when temp < |
|---|---|---|---|
| 0 | 0 | up_thr1 (51°C) | — |
| 1 | 80 | up_thr2 (61°C) | down_thr1 (36°C) |
| 2 | 120 | up_thr3 (71°C) | down_thr2 (52°C) |
| 3 | 160 | up_thr4 (82°C) | down_thr3 (62°C) |
| 4 | 255 | — | down_thr4 (72°C) |

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_fancontrol_557` | `tests/fancontrol/test_tc_fancontrol_557_details_of_temp_pwm.py` | grep returns non-empty timestamps from `temperature =` lines; `interval_check` confirms ~2s spacing between entries | — |
| `TC_fancontrol_574` | `tests/fancontrol/test_tc_fancontrol_574_interval_in_override.py` | Reads `fan.interval` from `bagheera_config.ini`; grep returns timestamps; `interval_check` confirms spacing matches the configured interval value | — |

---

### Flow 4: Log File Storage Location

**What happens:** `fan_control` initializes its log directory at startup via `nd_log_init(log_dir.c_str())` and `route_logs(log_dir.c_str())`. On krait/krait2 the logs are written to `/data/nd_files/log/fan/`. The test simply verifies that `.log` files exist at this path — confirming the service has been running and producing output.

**When active:** When `fan_enable=true` and service has been running at least one `interval` cycle
**Frequency:** Files created at service start
**Cross-service impact:** None

**Verification command:**
```bash
ls /data/nd_files/log/fan/*.log
# Must return at least one file with non-empty stdout
```

**Log path by device type:**
| Device | Log path |
|---|---|
| krait / krait2 | `/data/nd_files/log/fan/` |

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_fancontrol_614` | `tests/fancontrol/test_tc_fancontrol_614_logs_storage_location.py` | `ls /data/nd_files/log/fan/*.log` succeeds and stdout is non-empty | — |

---

### Flow 5: Low Power Wakeup (LPW) — Fan PWM = 0

**What happens:** When the device enters Low Power Wakeup (LPW) mode (ignition off → crank shutdown → LPW sleep), the CPU temperature drops and the fan state machine transitions to state 0 (PWM = 0). TC_673 configures short LPW durations (`lowpower_wakeup_duration=30`, `lowpower_wakeup_cycle_duration=3`, `crank_shutdown_duration=3`), triggers LPW by turning the relay off, waits for the device to enter LPW, then reads the last `fan PWM` value from the log. The test asserts it equals `"0"` — confirming the fan is off during low-power operation.

**When active:** When device transitions into LPW mode
**Frequency:** Per LPW entry
**Cross-service impact:** Depends on `power_monitor` for LPW timing; relay control triggers ignition off

**Config pushed for this test:**
```ini
[power]
lowpower_wakeup_duration = 30
lowpower_wakeup_cycle_duration = 3
crank_shutdown_duration = 3
```

**Relay control:**
```bash
relay off   # trigger ignition off → LPW entry
relay on    # restore after test
```

**Log verification command:**
```bash
grep -ir 'fan PWM' /data/nd_files/log/fan/*.log | tail -n 1 | awk -F' = ' '{print $NF}'
# Must return: 0
```

**Expected log line during LPW (from source — state 0):**
```
FAN: I: <pid>: <pid>: temperature = <N>, fan PWM = 0
```

**Test cases that validate this flow:**
| Test Case ID | Python Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_fancontrol_673` | `tests/fancontrol/test_tc_fancontrol_673_low_power_mode_validation.py` | Push LPW config, `relay off` → wait for LPW → grep last `fan PWM` value from `/data/nd_files/log/fan/*.log` == `"0"`; restore relay and config after | — |

---

## Config-Driven Flow Activation

The agent MUST check device type first — ALL fancontrol TCs exit NA if device is not krait or krait2.

| Config Section | Config Key | Value | Activates Flow(s) | Test Cases Affected |
|---|---|---|---|---|
| `[fan]` | `enable` | `true` (default) | Flows 3, 4, 5 (service runs) | TC_557, 569, 574, 612, 614, 615, 617, 673 |
| `[fan]` | `enable` | `false` | Flow 1 disabled path only | TC_571, 618 |
| `[fan]` | `interval` | `2` (default config) | Flow 3 timing | TC_557, 574 |
| `[power]` | `lowpower_wakeup_duration` | `30` (pushed by test) | Flow 5 LPW | TC_673 |
| device_type | `krait` / `krait2` | — | All flows | All TCs |
| device_type | anything else | — | No flows | All TCs → NA |

**Rules:**
- device_type check is always the first precondition — skip all flows and mark NA for non-krait devices
- `fan.enable=false` → only Flows 1 and 2 (disabled path) apply; Flows 3/4/5 will not produce log output
- TC_571 and TC_618 both push `fan.enable=false` and restore afterward — do not leave device in disabled state
- TC_673 pushes power config and must restore it after

---

## Cross-Service Dependencies

| Related Service | Why | When to check its logs |
|---|---|---|
| `power_monitor` | Controls LPW entry timing; `crank_shutdown_duration` and `lowpower_wakeup_duration` are power_monitor config keys | Flow 5 (TC_673) — verify device entered LPW before checking fan PWM |
| `bagheera_config.ini` / `bagheera_override.ini` | Config source for all `[fan]` parameters | All flows — read via `check_config_value`; written via `change_param_value` + `upload_config` |

---

## Flow Dependency Graph

```
service start
 └─► [Flow 1: Config Parsing]
       ├─► fan_enable=false → "Feature Disabled, Exiting" → service stops (TC_571, TC_618)
       └─► fan_enable=true
             ├─► [Flow 2: Service Status] (TC_615, TC_617, TC_618)
             ├─► [Flow 4: Log file created at /data/nd_files/log/fan/] (TC_614)
             └─► main loop every interval seconds:
                   └─► [Flow 3: temperature = N, fan PWM = N logged] (TC_557, TC_574)

LPW event (relay off → crank shutdown → LPW sleep)
 └─► CPU temp drops → fan state → 0 → PWM = 0
       └─► [Flow 5: last fan PWM = 0 in logs] (TC_673)

config push → restart_service / reboot
 └─► [Flow 1: re-read config] → [Flow 2: service active/inactive]
```

---

## Validation Instructions for the Agent

1. **Check device type first** — if not krait or krait2, mark all TCs as NA and stop
2. **Read `[fan] enable`** from `bagheera_config.ini` to confirm service is expected to be running
3. **For log pattern searches**, search `/data/nd_files/log/fan/*.log` using grep (not search_logs API — fan uses direct grep commands)
4. **For interval validation** (TC_557, TC_574): extract uptime_ms field (2nd colon-delimited field) from the last 10 `temperature =` log lines and verify ~`interval`-second spacing via `interval_check`
5. **For PWM value extraction** (TC_673): grep `fan PWM` from last log line and extract value after ` = `
6. **For config key validation** (TC_612): use `check_config_value("fan", "<key>", "bagheera_config.ini")` — exact string match against expected defaults
7. **Correct default values** (from source code — TC_612 ground truth):
   - `up_thr4 = 82` (not 81), `down_thr1 = 36`, `down_thr2 = 52`, `down_thr3 = 62`, `down_thr4 = 72`
8. **Report verdict** per test case: PASS / FAIL / NA
