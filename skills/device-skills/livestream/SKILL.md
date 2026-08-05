---
name: livestream
description: "Use when: starting a live stream on a test device, initiating inward/outward/dual camera livestream, triggering livestream via IDMS staging API. Handles the full flow: login → ops-data (vehicleId lookup) → livestream API call."
argument-hint: "camera direction and device info (e.g., 'start inward livestream on device 105512500007')"
---
# Livestream

Start a live video stream on a Netradyne test device by calling the IDMS staging API. Supports inward, outward, or dual camera modes.

## When to Use

- A test case requires starting a livestream on the device under test
- Pre-step or acceptance criteria mention "live stream", "livestream", or "on-demand video"
- The agent needs to verify livestream connectivity or response from the cloud

## Prerequisites

- **Device ID** — the numeric device ID (e.g. `105512500007`). Obtain from `deviceconfig.ini` on the device or pass as argument.
- **Device type** — e.g. `bagheera4`, `krait2`. Detected from `package_manifest.ini`.
- **Product variant** — e.g. `us`, `india`, `global`. Determined from OTA version string or device config.
- **Python `requests` package** installed (`pip install requests`).
- **Network access** to `auth-staging.netradyne.com` and `idms-staging.netradyne.com` from the host machine.

## Script Location

```
claude_device_validator/src/livestream.py
```

## Usage

```bash
python3 claude_device_validator/src/livestream.py <device_id> <device_type> <product_variant> [--camera inward|outward|dual] [-v]
```

### Arguments

| Argument               | Required | Description                                                |
| ---------------------- | -------- | ---------------------------------------------------------- |
| `device_id`          | Yes      | Numeric device ID (e.g.`105512500007`)                   |
| `device_type`        | Yes      | Device type:`bagheera4`, `krait2`, `bagheera3`, etc. |
| `product_variant`    | Yes      | Variant:`us`, `india`, `global`                      |
| `--camera`           | No       | Camera mode:`inward`, `outward`, or `dual` (default) |
| `-v` / `--verbose` | No       | Print detailed debug output                                |

### Examples

```bash
# Dual livestream (default)
python3 claude_device_validator/src/livestream.py 105512500007 bagheera4 us

# Inward camera only
python3 claude_device_validator/src/livestream.py 105512500007 bagheera4 us --camera inward

# Outward camera only with verbose logging
python3 claude_device_validator/src/livestream.py 105512500007 bagheera4 us --camera outward -v
```

## Procedure (What the Script Does)

### Step 1 — Authenticate

Sends a POST to the staging auth server to obtain an `access_token`, then creates a session to get a `session_key`.

- Endpoint: `https://auth-staging.netradyne.com/authserver/api/v1/oauth/token`
- Credentials: `device-test-automation` / `devicetestautomation`
- Retries up to 5 times with exponential backoff on failure.

### Step 2 — Resolve Vehicle ID

Calls the ops-data API with the `device_id` and `product_id` to look up the `vehicleId` needed for the livestream request.

- Endpoint: `https://idms-staging.netradyne.com/device-health/api/v1/opsdashboard/ops-data`
- The `product_id` is resolved from `device_type` + `product_variant` using a built-in mapping table.

### Step 3 — Start Livestream

Posts a livestream request to the on-demand API:

- Endpoint: `https://idms-staging.netradyne.com/restserver/api/v1/ondemand/liveStream/{vehicleId}?streamType={1|2}`

| Camera Mode | `streamType` | `camera` field |
| ----------- | -------------- | ---------------- |
| `inward`  | 1              | 1                |
| `outward` | 1              | 0                |
| `dual`    | 2              | _(omitted)_    |

Default parameters: `duration=1`, `bitRate=512`, `resolution=640*480`.

### Exit Codes

| Code | Meaning                           |
| ---- | --------------------------------- |
| 0    | Livestream started successfully   |
| 1    | Any failure (auth, ops-data, API) |

## Integration with Device Sanity Agent

When a test case contains a pre-step or acceptance criterion that requires livestreaming:

1. Detect `device_id` from the device: `adb -s <SERIAL> shell "grep -i deviceId /home/ubuntu/config/deviceconfig.ini"`
2. Detect `device_type` from `package_manifest.ini` (already known from device-type detection).
3. Determine `product_variant` from OTA version string (`us`, `india`, `global`).
4. Run the script and capture the exit code + stdout for the artifact.
5. Record the command and output in the artifact step:
   ```
   command: python3 claude_device_validator/src/livestream.py <device_id> <device_type> <variant> --camera <mode>
   ```

## Supported Product Variants

| Variant Key          | Product ID |
| -------------------- | ---------- |
| `bagheera_us`      | 2          |
| `bagheera_india`   | 4          |
| `bagheera2_us`     | 11         |
| `bagheera3_us`     | 15         |
| `bagheera3_global` | 16         |
| `bagheera4_us`     | 20         |
| `krait_us`         | 9          |
| `krait_india`      | 10         |
| `krait_global`     | 13         |
| `krait2_us`        | 12         |
| `krait2_global`    | 14         |
| `octo`             | 18         |
| `octo_global`      | 19         |

## Troubleshooting

| Symptom                       | Cause                                   | Fix                                             |
| ----------------------------- | --------------------------------------- | ----------------------------------------------- |
| `access_token missing`      | Auth server unreachable or creds wrong  | Check network, verify staging is up             |
| `session_id missing`        | Token valid but session creation failed | Retry; check auth-staging health                |
| `vehicleId not found`       | Device not provisioned in staging       | Verify device_id and product_id are correct     |
| `Unknown device variant`    | Missing entry in PRODUCT_ID_MAP         | Add the variant to the map in `livestream.py` |
| `Livestream request failed` | Device offline or AWS IoT not connected | Ensure device has valid AWS IoT certificates    |
