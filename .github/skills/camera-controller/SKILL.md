---
name: camera-controller
description: "Use when: checking connected cameras, verifying camera status in config, enabling/disabling cameras, checking physical camera connections on Octo devices. Ported from nd_test_bot (6.14_changes branch) camera_api.py."
argument-hint: "action and params (e.g., check_connected_cameras_octo [0,1,2,3], check_cam_status_in_config 1, enable_disable_camera [0,1] true)"
---

# Camera Controller

Check camera status, enable/disable cameras, and verify physical camera connections. Platform-aware for Octo (8 cameras), Bagheera (2-4 cameras), and Krait devices.

Source: Ported from nd_test_bot repo, branch 6.14_changes, file `Test_Automation_Framework/Lib/apis/camera_api.py`.

## Camera Numbering

| Cam Num | Position | Config Section (Bagheera) | Config Section (Octo) |
|---------|----------|--------------------------|----------------------|
| 0 | Outward/Front | `[camera]` cam0_enable | `[camera]` cam0_enable |
| 1 | Inward/Back | `[camera]` cam1_enable | `[camera]` cam1_enable |
| 2 | Right | `[camera]` cam2_enable | `[camera]` cam2_enable |
| 3 | Left | `[camera]` cam3_enable | `[camera]` cam3_enable |
| 4-7 | Aux cameras (Octo only) | N/A | `[aux_cam]` cam{N}_enable |

## Supported Actions

### check_cam_status_in_config

Check whether a camera is enabled/disabled in the device config.

**Parameters:**
- `cam_num` — camera number (0-3 for bagheera, 0-7 for octo)

**Implementation (Bagheera/Krait):**
```bash
# Read bagheera_config.ini
cat <CONFIG_ROOT>/bagheera_config.ini | grep -A50 "\[camera\]" | grep "cam<N>_enable"
```

**Implementation (Octo):**
```bash
# Cams 0-3: [camera] section in bagheera_override.ini
cat <CONFIG_ROOT>/bagheera_override.ini | grep -A50 "\[camera\]" | grep "cam<N>_enable"
# Cams 4-7: [aux_cam] section
cat <CONFIG_ROOT>/bagheera_override.ini | grep -A50 "\[aux_cam\]" | grep "cam<N>_enable"
```

**Returns:** `(status, enabled_value)` — e.g., `("Pass", "true")` or `("Pass", "false")`

### check_connected_cameras_octo

Check which cameras are both enabled in config AND physically connected on an Octo device.

**Parameters:**
- `cam_nums` — list of camera numbers to check (default: `[0,1,2,3,4,5,6,7]`)

**Implementation:**
```bash
# 1. For each cam_num, check if enabled in config
cat <CONFIG_ROOT>/bagheera_override.ini | grep "cam<N>_enable"

# 2. For cams 0-3: assumed physically connected if enabled
# 3. For cams 4-7: check physical connection
#    Upload and run check script:
cat /home/ubuntu/check_camera.sh  # or create script
# The script checks: "Acam-{N} is connected"
```

**Returns:** `(status, connected_cameras_list)` — e.g., `("Pass", [0, 1])`

**Pass/Fail:** PASS if at least one camera is connected. The returned list tells which cameras are available.

### check_physical_octo_camera_connection

Check if a specific aux camera (4-7) is physically connected on Octo.

**Parameters:**
- `cam_num` — aux camera number (4-7)

**Implementation:**
```bash
# Upload check script to device, run it, look for "Acam-{N} is connected"
# Script uses v4l2-ctl or similar to probe camera hardware
```

### enable_disable_camera

Enable or disable one or more cameras by modifying the config.

**Parameters:**
- `cam_nums` — list of camera numbers
- `new_value` — `"true"` or `"false"`

**Implementation:**
```bash
# Use sed to modify config in-place
sed -i "s/cam<N>_enable.*/cam<N>_enable = <new_value>/" <CONFIG_ROOT>/bagheera_override.ini
```

**Note:** Requires reboot or service restart for changes to take effect.

## Config Paths

| Device Type | Config Root |
|-------------|------------|
| bagheera / bagheera2 / bagheera3 / octo | `/home/ubuntu/config/` |
| krait / krait2 / bagheera4 | `/data/nd_files/config/` |

## Usage in Test Cases

Typical flow:
1. `check_connected_cameras_octo` → get list of available cameras
2. Use the list to determine which file checks to perform (e.g., only check files for connected cameras)
3. Camera count affects expected file counts in file generation checks
