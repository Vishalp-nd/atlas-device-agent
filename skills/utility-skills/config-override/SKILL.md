---
name: config-override
description: "Use when: modifying bagheera_override.ini on a device via ADB, applying config from project config files, pushing override config files. Handles the full flow: read config from claude_device_validator/config/ → ADB pull → local INI merge → ADB push → reboot device."
argument-hint: "section key value (e.g., /config-override privacy_mode default_privacy true)"
---

# Config Override

Pull `bagheera_override.ini` from a connected device via ADB, modify parameters locally using Python's `configparser`, and push it back. This replicates the `Config_api.append_config_content` / `change_param_value` workflow without SSH — using only ADB.

## When to Use

- Change a config parameter in `bagheera_override.ini` before running a test
- Append a new section/key to the override config
- Any test pre-step that requires config modification on the device

## Prerequisites

- Device connected via ADB (`adb devices` shows the serial)
- Python 3 available on the host machine
- The device runs Ubuntu Linux (Bagheera or Krait family)

## Device Paths

The override file location depends on the device type:

| Device Type | Override Path |
|-------------|---------------|
| bagheera | `/home/ubuntu/config/bagheera_override.ini` |
| bagheera2 | `/home/ubuntu/config/bagheera_override.ini` |
| bagheera3 | `/home/ubuntu/config/bagheera_override.ini` |
| bagheera4 | `/data/nd_files/config/bagheera_override.ini` |
| krait | `/data/nd_files/config/bagheera_override.ini` |
| krait2 | `/data/nd_files/config/bagheera_override.ini` |

If unsure, detect the device type first:
```bash
adb -s <SERIAL> shell "grep -i device_type /home/ubuntu/.nddevice/latest/package_manifest.ini"
```

Then resolve the path:
- If device type contains `krait` or `bagheera4` → `/data/nd_files/config/bagheera_override.ini`
- Otherwise → `/home/ubuntu/config/bagheera_override.ini`

## Procedure

### Step 1 — Determine the override path on the device

```bash
DEVICE_TYPE=$(adb -s <SERIAL> shell "grep -i device_type /home/ubuntu/.nddevice/latest/package_manifest.ini" | cut -d= -f2 | tr -d '\r\n ')
```

Set `OVERRIDE_PATH` based on `DEVICE_TYPE`:
- `krait`, `krait2`, `bagheera4` → `/data/nd_files/config/bagheera_override.ini`
- `bagheera`, `bagheera2`, `bagheera3` → `/home/ubuntu/config/bagheera_override.ini`

### Step 2 — Check if the override file exists; create if missing

```bash
adb -s <SERIAL> shell "ls <OVERRIDE_PATH>" 2>&1
```

- If the file **does not exist**, create an empty one:
  ```bash
  adb -s <SERIAL> shell "touch <OVERRIDE_PATH>"
  ```
- If it exists, proceed to pull.

### Step 3 — Pull the override file to localhost

Create a local working directory and pull the file:

```bash
mkdir -p /tmp/config_override_<SERIAL>
adb -s <SERIAL> pull <OVERRIDE_PATH> /tmp/config_override_<SERIAL>/bagheera_override.ini
```

Also back up the original for potential restore:
```bash
cp /tmp/config_override_<SERIAL>/bagheera_override.ini /tmp/config_override_<SERIAL>/bagheera_override_backup.ini
```

### Step 4 — Modify the config locally

The config content to apply is defined in an INI file under the project's config directory:

```
claude_device_validator/config/<CONFIG_FILENAME>
```

The `<CONFIG_FILENAME>` comes from the test case's `config_override_files` list (e.g., `bagheera_override_1413.ini`).

#### Read the config file and merge into the device override:

```bash
python3 -c "
import configparser

# Load the device's current override
config = configparser.ConfigParser()
config.read('/tmp/config_override_<SERIAL>/bagheera_override.ini')

# Load the config content from the project config file
content = configparser.ConfigParser()
content.read('claude_device_validator/config/<CONFIG_FILENAME>')

# Merge: add/overwrite sections and keys
for section in content.sections():
    if not config.has_section(section):
        config.add_section(section)
    for key, value in content.items(section):
        config.set(section, key, value)

with open('/tmp/config_override_<SERIAL>/bagheera_override.ini', 'w') as f:
    config.write(f)
print('Config merged from <CONFIG_FILENAME>')
"
```

If the test case lists **multiple** config files in `config_override_files`, merge each one in order.

#### Example config file (`config/bagheera_override_06.ini`):
```ini
[driverlogin_v2]
enabled = true
enable_audio_reminders = true
```

### Step 5 — Push the modified file back to the device

```bash
adb -s <SERIAL> push /tmp/config_override_<SERIAL>/bagheera_override.ini <OVERRIDE_PATH>
```

Verify the push succeeded:
```bash
adb -s <SERIAL> shell "cat <OVERRIDE_PATH> | grep -i <KEY>"
```

### Step 6 — Reboot the device and wait for it to come back up

After pushing the config, reboot the device so the changes take effect:

```bash
adb -s <SERIAL> reboot
```

Wait 20 seconds for the device to come back up, then verify ADB connectivity:

```bash
# Wait for device to reboot
sleep 20
# Confirm device is back online
adb -s <SERIAL> wait-for-device
adb -s <SERIAL> shell "echo 'Device is back up'"
```

### Step 7 — Report the result

After reboot, report:
```
Config Override Result:
  Device:    <SERIAL>
  File:      <OVERRIDE_PATH>
  Config:    <CONFIG_FILENAME> (from claude_device_validator/config/)
  Reboot:    Completed (waited 20s)
  Status:    Success
```

## Cleanup (optional)

If a test needs to restore the original override file after completion:

```bash
adb -s <SERIAL> push /tmp/config_override_<SERIAL>/bagheera_override_backup.ini <OVERRIDE_PATH>
```

Or if the override file was newly created (didn't exist before) and should be removed:
```bash
adb -s <SERIAL> shell "rm <OVERRIDE_PATH>"
```

## Example

The test case specifies `config_override_files: [bagheera_override_06.ini]`.

Config file at `config/bagheera_override_06.ini`:
```ini
[driverlogin_v2]
enabled = true
enable_audio_reminders = true
```

Execution on device `2543fa04`:

```bash
# 1. Detect device type
adb -s 2543fa04 shell "grep -i device_type /home/ubuntu/.nddevice/latest/package_manifest.ini"
# → device_type=bagheera4  →  path = /data/nd_files/config/bagheera_override.ini

# 2. Pull
mkdir -p /tmp/config_override_2543fa04
adb -s 2543fa04 pull /data/nd_files/config/bagheera_override.ini /tmp/config_override_2543fa04/bagheera_override.ini
cp /tmp/config_override_2543fa04/bagheera_override.ini /tmp/config_override_2543fa04/bagheera_override_backup.ini

# 3. Merge config from project file
python3 -c "
import configparser
config = configparser.ConfigParser()
config.read('/tmp/config_override_2543fa04/bagheera_override.ini')
content = configparser.ConfigParser()
content.read('claude_device_validator/config/bagheera_override_06.ini')
for section in content.sections():
    if not config.has_section(section):
        config.add_section(section)
    for key, value in content.items(section):
        config.set(section, key, value)
with open('/tmp/config_override_2543fa04/bagheera_override.ini', 'w') as f:
    config.write(f)
print('Done')
"

# 4. Push back
adb -s 2543fa04 push /tmp/config_override_2543fa04/bagheera_override.ini /data/nd_files/config/bagheera_override.ini

# 5. Reboot and wait
adb -s 2543fa04 reboot
sleep 20
adb -s 2543fa04 wait-for-device
adb -s 2543fa04 shell "echo 'Device is back up'"
```

## Removing a Config Parameter

Some tests require removing a key from the override so the device falls back to the base config value. Use `configparser.remove_option()`:

```bash
python3 -c "
import configparser
config = configparser.ConfigParser()
config.read('/tmp/config_override_<SERIAL>/bagheera_override.ini')
config.remove_option('<SECTION>', '<KEY>')
# If the section is now empty, optionally remove it:
if config.has_section('<SECTION>') and not config.options('<SECTION>'):
    config.remove_section('<SECTION>')
with open('/tmp/config_override_<SERIAL>/bagheera_override.ini', 'w') as f:
    config.write(f)
print('Removed <SECTION>/<KEY>')
"
```

After removal, push the file back and either reboot or restart the relevant service:

```bash
adb -s <SERIAL> push /tmp/config_override_<SERIAL>/bagheera_override.ini <OVERRIDE_PATH>
# Option A: full reboot
adb -s <SERIAL> reboot
# Option B: restart specific service (no reboot needed)
adb -s <SERIAL> shell "systemctl restart <SERVICE_NAME>"
```

## Constraints

- Always back up the original before modifying.
- Do NOT modify `bagheera_config.ini` — only the override file.
- Do NOT delete the override file unless explicitly asked.
- Use `configparser` for INI manipulation — do not use sed/awk on INI files.
