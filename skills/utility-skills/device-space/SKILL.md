---
name: device-space
description: "Use when: getting available disk space, getting disk info for all mount points, creating large files to fill disk, searching for or deleting large files, managing disk space for storage tests. Ported from nd_test_bot (6.14_changes branch) device_space_api.py."
argument-hint: "action (e.g., get_available_space /, get_disk_info, create_large_file 1 G /tmp/fill.bin, delete_large_file /tmp/fill.bin)"
---

# Device Space

Manage and inspect disk space on the device: check available space, get full disk info, create/search/delete large files for storage tests.

Source: Ported from nd_test_bot repo, branch 6.14_changes, file `Test_Automation_Framework/Lib/apis/device_space_api.py`.

## Supported Actions

### get_available_space

Get available disk space for a specific mount point.

**Parameters:**
- `mount_point` — filesystem mount point (e.g., `/`, `/media/data/nd_sdcard`, `/data`)

**Implementation:**
```bash
df -h | grep -w "<mount_point>"
# Parse: Available column (typically column 4)
# Convert to GB float
```

**Returns:** `("Pass", device_type, available_gb)` — e.g., `("Pass", "bagheera3", 12.5)`

**Note:** Bagheera devices do NOT have a separate `/data` partition. Krait/bagheera4 do.

### get_disk_info

Get full disk information for all mount points.

**Implementation:**
```bash
df -h
```

**Returns:** Dictionary with keys per mount point, each containing: `Filesystem`, `Size`, `Used`, `Avail`, `Use%`, `Mounted_on`.

### get_disk_info_value

Extract a specific value from disk info.

**Parameters:**
- `disk_info` — dict from `get_disk_info()`
- `mount_point` — target mount point (default: `/`)
- `info_key` — key to extract: `Filesystem`, `Size`, `Used`, `Avail`, `Use%`

### create_large_file

Create a large file to fill disk space (for storage pressure tests).

**Parameters:**
- `size` — numeric size
- `unit` — unit: `K`, `M`, `G`
- `file_path` — absolute path on device for the file

**Implementation:**
```bash
# Krait/bagheera4 (no sudo needed):
fallocate -l <size><unit> <file_path>
chmod 666 <file_path>

# Bagheera/Octo (sudo):
sudo fallocate -l <size><unit> <file_path>
sudo chmod 666 <file_path>
```

### search_large_file

Check if a large file exists on device.

**Parameters:**
- `file_path` — path to the file
- `revert` — if `true`, PASS when file NOT found, FAIL when found

**Implementation:**
```bash
ls <file_path>
```

### delete_large_file

Delete a large file from device (cleanup after storage test).

**Parameters:**
- `file_path` — path to file

**Implementation:**
```bash
# Check exists first
ls <file_path>
# Delete
sudo rm -v <file_path>
```

## Common Mount Points

| Mount Point | Description | Device Types |
|-------------|-------------|-------------|
| `/` | Root filesystem | All |
| `/data` | Data partition | Krait, bagheera4 |
| `/media/data/nd_sdcard` | SD card | All (when SD present) |
| `/home` | Home directory | Bagheera family |
| `/var/log` | System logs | All |

## Usage in Test Cases

Typical storage test flow:
1. `get_available_space /` → check initial space
2. `create_large_file` → fill disk to near capacity
3. Run test scenario (e.g., verify circular buffer behavior under low space)
4. `delete_large_file` → cleanup
5. `get_available_space /` → verify space restored
