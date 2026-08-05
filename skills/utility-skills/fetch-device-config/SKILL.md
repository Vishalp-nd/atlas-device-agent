---
name: fetch-device-config
description: "Use when: fetching device configuration from the production IDMS database for one or more device IDs, storing configs as INI files in device_data/, reading device-specific config values for test case validation. Handles the full flow: query prod DB → convert JSON config to INI → save per-device files → read config values for test execution."
argument-hint: "device IDs (e.g., /fetch-device-config 12345 67890)"
---
# Fetch Device Config from Production DB

Fetch the active configuration for one or more devices from the production IDMS database (`nddynamicconfigurations` table), convert the JSON config to INI format, and store one file per device under `claude_device_validator/device_data/`.

## When to Use

- Before running test cases when the user provides device IDs but no config files
- When the **log-validator** agent needs to know a device's production config to validate against
- When test cases reference config-dependent thresholds (e.g., observation call interval, privacy mode settings) and you need the actual device config to determine expected values
- Any time the user says "fetch config", "get device config", or provides device IDs for validation

## Prerequisites

- Python 3 with `psycopg2` installed (`pip install psycopg2-binary`)
- Network access to `pg-production-ro.netradyne.info` or `pg-staging.0.netradyne.info` (VPN required)
- Database credentials file: `claude_device_validator/db_credentials.ini` (must have `[PROD_DB]` and `[STAG_DB]` sections)
- `expect` and `oathtool` installed on the host (for VPN auto-connect)

## Procedure

### Step 0 — Ensure OpenVPN is connected

Before querying the production database, verify the host is connected to the Netradyne VPN.

**Check if OpenVPN is running:**

```bash
pgrep -x openvpn > /dev/null 2>&1 && echo "VPN_CONNECTED" || echo "VPN_NOT_CONNECTED"
```

- If the output is `VPN_CONNECTED` → proceed to Step 1.
- If the output is `VPN_NOT_CONNECTED` → run the VPN connect script:

```bash
cd claude_device_validator/src
expect iravath.sh
```

After running, wait 5 seconds and verify again:

```bash
sleep 5 && pgrep -x openvpn > /dev/null 2>&1 && echo "VPN_CONNECTED" || echo "VPN_STILL_NOT_CONNECTED"
```

If still not connected, report the error to the user: *"Failed to connect to OpenVPN. Please connect manually and retry."*

### Step 1 — Run the fetch script

Execute `fetch_device_config.py` with the device IDs and target environment:

```bash
cd claude_device_validator/src
python fetch_device_config.py --device-ids <ID1> <ID2> <ID3> ... --env <prod|staging>
```

**Flags:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--device-ids` | Yes | — | One or more device IDs |
| `--env` | No | `prod` | Target environment: `prod` (production DB) or `staging` (staging DB) |
| `--db-config` | No | `../db_credentials.ini` | Path to DB credentials file |
| `--output-dir` | No | `../device_data/` | Output directory for INI files |

**The `--env` flag maps to DB sections:**
- `prod` / `production` → `[PROD_DB]` → `beta-prod-idms-db` at `pg-production-ro.netradyne.info`
- `staging` / `stag` → `[STAG_DB]` → `netradyne-testing` at `pg-staging.0.netradyne.info`

**Examples:**

```bash
# Production devices (default)
python fetch_device_config.py --device-ids 440073 440112 440200

# Staging devices
python fetch_device_config.py --device-ids 440073 440112 --env staging
```

This creates:

```
device_data/
  device_440073_config.ini
  device_440112_config.ini
  device_440200_config.ini
  device_list_config.csv          ← combined CSV with all devices
```

### Step 2 — Verify the output

After the script runs, verify the files were created:

```bash
ls -la device_data/
```

#### INI Files (per-device)

Each INI file has a `[_metadata]` section with `device_id` and `config_version`, followed by all config sections from the production database. Example:

```ini
[_metadata]
device_id = 440073
config_version = 1413
environment = production

[privacy_mode]
default_privacy = regular

[observation]
call_time_diff = 20

[driverlogin_v2]
enabled = true
...
```

#### CSV File (combined — `device_list_config.csv`)

A single CSV file with one row per device and one column per config section. This is the **primary file for determining which configs are enabled** on a device.

**Columns:**

| Column | Description |
|--------|-------------|
| `Device_ID` | The device's manufacturer device ID |
| `OTA_Version` | Currently installed OTA version |
| `Config_Version` | Pushed merge config version from IDMS |
| `tenant_id` | Tenant ID the device belongs to |
| `tenant_display_name` | Tenant display name |
| `tenant_unique_name` | Tenant unique name |
| `Config` | Full raw config JSON string |
| `Device_State` | Device state code from IDMS |
| `<section_name>` ... | One column per config section — contains the section's key-value pairs as a JSON dict, or empty if that section is not present in the device's config |

**Example CSV row:**

```csv
Device_ID,OTA_Version,Config_Version,tenant_id,...,observation,privacy_mode,driverlogin_v2
440073,5.6.14.rc.3,1413,42,...,"{""call_time_diff"": ""20"", ""enabled"": ""true""}","{""default_privacy"": ""regular""}","{""enabled"": ""true""}"
```

**Reading the CSV to check if a config section is enabled:**

```python
import csv, json

def read_device_config_csv(device_id, csv_path="device_data/device_list_config.csv"):
    """Read config sections for a specific device from the CSV."""
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["Device_ID"] == str(device_id):
                return row
    return None

# Get a specific section's values
row = read_device_config_csv("440073")
if row and row.get("observation"):
    obs_config = json.loads(row["observation"])
    call_time_diff = obs_config.get("call_time_diff", "10")
```

### Step 3 — Read config values for test validation

When executing or validating test cases, read the device config from either:
- **CSV** (`device_data/device_list_config.csv`) — use for checking which config sections are enabled/present and for batch device analysis
- **INI** (`device_data/device_<ID>_config.ini`) — use for per-device config value lookups

#### Reading config from CSV (preferred for section presence checks):

```python
import csv, json
with open('device_data/device_list_config.csv', 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row['Device_ID'] == '<DEVICE_ID>':
            # Check if a section exists and is non-empty
            if row.get('observation'):
                config = json.loads(row['observation'])
                value = config.get('call_time_diff', '10')
```

#### Reading config from INI:

```python
import configparser
config = configparser.ConfigParser()
config.read('device_data/device_<DEVICE_ID>_config.ini')
value = config.get('section_name', 'key_name', fallback='default_value')
```

#### Reading config via shell:

```bash
grep -A 20 '\[observation\]' device_data/device_<DEVICE_ID>_config.ini
```

## Config-Aware Test Case Validation

**CRITICAL**: When validating test cases against a device's config, the expected values come from the device's INI config file — NOT from hardcoded defaults in the test case definition.

### Example: Observation Call Time Difference

Test case TC_01 checks observation call time differences. The default interval is `10` seconds, but a device's config may override this.

**Wrong approach** (hardcoded default):

```
Check that observation calls have a time diff of exactly 10 seconds → FAIL
```

**Correct approach** (config-aware):

1. Read `device_data/device_<ID>_config.ini`
2. Get `[observation] call_time_diff` → finds `20`
3. Validate that observation calls have a time diff of `20` seconds → PASS

### General Rule

For every acceptance criterion that references a configurable value:

1. **Identify the config section and key** from the acceptance criteria or test description
2. **Read the device's INI config** from `device_data/device_<ID>_config.ini`
3. **Use the config value** (or fallback to the system default if the key is absent)
4. **Validate against the actual config value**, not a hardcoded one

### Config Sections Commonly Checked

| Section              | Common Keys                                   | Used By                  |
| -------------------- | --------------------------------------------- | ------------------------ |
| `[observation]`    | `call_time_diff`, `enabled`               | TC_01, observation tests |
| `[privacy_mode]`   | `default_privacy`, `privacy_mode_timeout` | TC_1413, privacy tests   |
| `[driverlogin_v2]` | `enabled`, `enable_audio_reminders`       | Driver login tests       |
| `[power]`          | `ignition_off_timeout`, `sleep_mode`      | Power management tests   |
| `[upload_video]`   | `enabled`, `upload_interval`              | Video upload tests       |
| `[cam_rec]`        | `resolution`, `fps`                       | Camera recording tests   |

## INI File Format

Each device config INI file follows standard Python `configparser` format:

```ini
[_metadata]
device_id = <DEVICE_ID>
config_version = <VERSION_NUMBER>

[section_name]
key = value
another_key = another_value

[another_section]
...
```

- The `[_metadata]` section is auto-added and contains the device ID and config version from the database
- All other sections come directly from the production `nddynamicconfigurations.config_json` field
- Section names and keys preserve their original casing from the database
- Values are stored as strings (convert to int/bool as needed when reading)

## Error Handling

| Error                                   | Cause                               | Fix                                                                  |
| --------------------------------------- | ----------------------------------- | -------------------------------------------------------------------- |
| `Section 'PROD_DB' not found`         | Missing or wrong db_credentials.ini | Verify file exists at `claude_device_validator/db_credentials.ini` |
| `Failed to connect to database`       | No VPN / wrong credentials          | Check VPN connection and credentials                                 |
| `No devices found with the given IDs` | Invalid device IDs                  | Verify device IDs are correct (numeric)                              |
| `Config version X has empty config`   | Device has no dynamic config        | Device may use only base config (no override)                        |

## Integration with Log Validator

When the **log-validator** agent receives device IDs:

1. **First**, invoke this skill to fetch and store configs → `device_data/device_<ID>_config.ini`
2. **Then**, for each test case, read the device config to determine expected values
3. **Use the config values** as the ground truth when evaluating acceptance criteria
4. **Report mismatches** between log evidence and the config-defined expected values

This ensures the validator checks against the **actual production config** running on the device, not against default/assumed values.
