---
name: cloud-api
description: "Use when: sending AWS IoT reboot command, AWS IoT ping command, requesting VOD from cloud, switching device auth method, pushing override config to cloud. Ported from nd_test_bot (6.14_changes branch) cloud_api.py."
argument-hint: "action (e.g., aws_reboot, aws_ping_command 'restart-bagheera', switch_auth_method jwt, vod_request)"
---

# Cloud API

Send commands to the device via cloud APIs (IDMS staging): AWS IoT reboot, ping commands, VOD requests, and auth method switching.

Source: Ported from nd_test_bot repo, branch 6.14_changes, file `Test_Automation_Framework/Lib/apis/cloud_api.py`.

## Authentication

All cloud APIs require authentication against `auth-staging.netradyne.com`:

```bash
# 1. Login to get tokens
curl -X POST "https://auth-staging.netradyne.com/auth/api/v1/token" \
  -H "Content-Type: application/json" \
  -d '{"email": "<EMAIL>", "password": "<PASSWORD>"}'
# Returns: { "session_key": "...", "access_token": "..." }
```

Credentials come from environment or device config.

## Supported Actions

### aws_reboot

Send a reboot command to the device via AWS IoT / IDMS API.

**Implementation:**
```bash
curl -X POST "https://idms-staging.netradyne.com/restserver/api/v1/devices/<DEVICE_ID>/ping" \
  -H "Content-Type: application/json" \
  -H "session-key: <SESSION_KEY>" \
  -H "access-token: <ACCESS_TOKEN>" \
  -d '{"commands": ["reboot-phone"]}'
```

**Expected:** Device reboots within 30-60 seconds. Verify by checking uptime after.

### aws_ping_command

Send an arbitrary ping command to the device via AWS IoT.

**Parameters:**
- `ping_command` — command string (e.g., `restart-bagheera`, `restart-power_monitor`)

**Implementation:**
```bash
curl -X POST "https://idms-staging.netradyne.com/restserver/api/v1/devices/<DEVICE_ID>/ping" \
  -H "Content-Type: application/json" \
  -H "session-key: <SESSION_KEY>" \
  -H "access-token: <ACCESS_TOKEN>" \
  -d '{"commands": ["<PING_COMMAND>"]}'
```

### switch_auth_method

Switch the device authentication method (e.g., from certificate to JWT or vice versa).

**Parameters:**
- `auth_method` — desired auth method (e.g., `jwt`, `certificate`)

**Implementation:**
```bash
curl -X POST "https://idms-staging.netradyne.com/restserver/opsdashboard/switch-device-auth-method/" \
  -H "Content-Type: application/json" \
  -H "session-key: <SESSION_KEY>" \
  -H "access-token: <ACCESS_TOKEN>" \
  -d '{"device_id": "<DEVICE_ID>", "desired_auth_method": "<AUTH_METHOD>"}'
```

### vod_request

Request Video-on-Demand from cloud for a time range.

**Parameters:**
- `start_time` — epoch timestamp (start of video range)
- `end_time` — epoch timestamp (end of video range)
- `video_count` — expected number of videos (optional)

**Implementation:**
1. Call `ops_data_api()` to get device operational data
2. Call `getVideoList(start_time, end_time)` to get video catalog
3. POST to `/ondemand/request` with the catalog IDs:
```bash
curl -X POST "https://idms-staging.netradyne.com/restserver/api/v1/ondemand/request" \
  -H "Content-Type: application/json" \
  -H "session-key: <SESSION_KEY>" \
  -H "access-token: <ACCESS_TOKEN>" \
  -d '{"fetchVideoList": [<CATALOG_IDS>], "fetchReason": "Alert"}'
```

## Notes

- All API calls go to IDMS **staging** environment (`idms-staging.netradyne.com`)
- Device ID comes from the device's `nddevice.ini` or automation config
- Session key and access token expire — re-login if 401 returned
- AWS IoT commands are asynchronous — the device processes them when connected
