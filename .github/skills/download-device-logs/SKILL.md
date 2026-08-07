---
name: download-device-logs
description: "Use when: downloading device logs from AWS S3 for one or more device IDs given a date range and environment (prod/staging). This is the FIRST step before config fetch or test case validation. Downloads, extracts, and merges logs into device_logs/<device_id>/."
argument-hint: "device IDs, start date, end date, environment (e.g., /download-device-logs 440073 2026-04-20 2026-04-24 prod)"
---

# Download Device Logs from AWS S3

Download device log archives from the Netradyne S3 buckets (`idms-production` or `idms-staging`), extract and merge them, and store flat log files per device under `claude_device_validator/device_logs/<device_id>/`.

## When to Use

- **ALWAYS** as the first step when the log-validator agent receives device IDs
- Before fetching device config (this runs first, config fetch runs second)
- When the user provides device IDs with a date range for log validation
- Any time the user says "download logs", "get logs", or "validate device" with device IDs

## Prerequisites

- AWS credentials configured (`~/.aws/credentials` with `default` profile, region `us-west-2`)
- OpenVPN connected (check via `pgrep -x openvpn`; if not, run `expect iravath.sh` from `claude_device_validator/src/`)
- Airavath2.0 installed at `~/Documents/Airavath2.0/Airavath/` (provides S3Manager and extractor)
- Python 3 with `boto3` installed

## Procedure

### Step 0 — Ensure OpenVPN is connected

```bash
pgrep -x openvpn > /dev/null 2>&1 && echo "VPN_CONNECTED" || echo "VPN_NOT_CONNECTED"
```

- If `VPN_CONNECTED` → proceed to Step 1.
- If `VPN_NOT_CONNECTED` → connect:

```bash
cd claude_device_validator/src
expect iravath.sh
```

Wait and verify:
```bash
sleep 5 && pgrep -x openvpn > /dev/null 2>&1 && echo "VPN_CONNECTED" || echo "VPN_STILL_NOT_CONNECTED"
```

### Step 1 — Run the download script

```bash
cd claude_device_validator/src
python download_device_logs.py --device-ids <ID1> <ID2> ... --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD> --env <prod|staging>
```

**Flags:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--device-ids` | Yes | — | One or more device IDs |
| `--start-date` / `-sd` | Yes | — | Start date (YYYY-MM-DD) |
| `--end-date` / `-ed` | Yes | — | End date (YYYY-MM-DD) |
| `--env` | No | `prod` | `prod` or `staging` — determines S3 bucket |

**Environment → S3 bucket mapping:**
- `prod` / `production` → `idms-production`
- `staging` / `stag` → `idms-staging`

**Example:**

```bash
# Production device logs for April 20-24
python download_device_logs.py --device-ids 440073 440112 --start-date 2026-04-20 --end-date 2026-04-24 --env prod

# Staging device logs
python download_device_logs.py --device-ids 440073 --start-date 2026-04-22 --end-date 2026-04-24 --env staging
```

### Step 2 — Verify downloaded logs

```bash
ls -la claude_device_validator/device_logs/<DEVICE_ID>/
```

The output directory contains flat log files per device:

```
device_logs/
  440073/
    ndcentral.log
    power_mon.log
    svc.log
    cam_rec.log
    inference.log
    unifieduploader.log
    ...
  440112/
    ndcentral.log
    power_mon.log
    ...
```

- Each log file contains merged data across all dates in the range
- Archives (.7z, .zip) are automatically cleaned up after extraction
- If a device has no logs for a given date, it is skipped silently

## Output Format

Logs are stored as flat files under `device_logs/<device_id>/`:

```
device_logs/<device_id>/
  analytics.log
  apm.log
  awsiot.log
  cam_rec.log
  conn_mgr.log
  gps.log
  inference.log
  ndcentral.log
  power_mon.log
  svc.log
  unifieduploader.log
  uploader.log
  ...
```

These are the **merged, extracted** log files ready for the log-validator agent to search through.

## Error Handling

| Error | Cause | Fix |
|-------|-------|-----|
| `botocore.exceptions.NoCredentialsError` | No AWS credentials configured | Run `aws configure` or check `~/.aws/credentials` |
| `botocore.exceptions.ClientError: AccessDenied` | Wrong AWS profile or no S3 access | Check AWS profile has access to the S3 bucket |
| `0 file(s)` for all dates | Device ID wrong or no logs uploaded | Verify device ID; device may not have uploaded logs for that date range |
| Corrupted log warning | Archive download was incomplete | Script auto-retries up to 3 times per device/date |
| `VPN_NOT_CONNECTED` | OpenVPN not running | Run `expect iravath.sh` from `claude_device_validator/src/` |

## Integration with Log Validator

This skill is step 1 in the log-validator pipeline:

1. **download-device-logs** → download logs from S3 to `device_logs/<device_id>/`
2. **fetch-device-config** → fetch config from DB to `device_data/device_<ID>_config.ini`
3. **Run test cases** → validate logs against config-aware acceptance criteria
