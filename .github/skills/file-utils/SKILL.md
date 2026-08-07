---
name: file-utils
description: "Use when: checking if a file exists on device, getting current session name from iriscli/files, getting file permissions, checking OTA version, validating audio files against CSV reference, checking files exist in a path. Ported from nd_test_bot (6.14_changes branch) file_utils.py."
argument-hint: "action and params (e.g., file_availability /path/to/file, get_current_session_name, check_audio_files D450_audio.csv)"
---

# File Utils

File existence checks, session name retrieval, permission inspection, OTA version validation, and audio file integrity checks on device.

Source: Ported from nd_test_bot repo, branch 6.14_changes, file `Test_Automation_Framework/Lib/apis/file_utils.py`.

## Supported Actions

### file_availability

Check if a file or path exists on device.

**Parameters:**
- `file_path` — absolute path on device

**Implementation:**
```bash
ls <file_path> 2>/dev/null
```

**Returns:** `("Pass", file_name)` if exists, `("Fail", "")` if not found.

### get_current_session_name

Get the current/latest session name from the iriscli files directory.

**Parameters:**
- `extension` — file extension filter (optional)
- `Cam_num` — camera number filter (default: 10 = any)
- `path` — custom path (default: `/home/iriscli/files/`)

**Implementation:**
```bash
# Get latest session name from files directory
ls -t /home/iriscli/files/ | head -20
# Extract session pattern: _trip<NNN>_y
ls -t /home/iriscli/files/ | grep -oP '_trip\d+_y' | head -1
```

For krait/krait2:
```bash
ls -t /data/nd_files/iriscli/files/ | head -20
```

**Returns:** `("Pass", "session_name")` — e.g., `("Pass", "_trip042_y")`

### get_file_permissions

Get the permissions string for a file on device.

**Parameters:**
- `file_path` — absolute path to file on device

**Implementation:**
```bash
ls -lrth <file_path> | cut -d ' ' -f1
```

**Returns:** `("Pass", "-rw-r--r--")` or similar permissions string.

### check_ota_version_higher

Check if the device OTA version is >= 6.10.

**Implementation:**
```bash
ls -lrth /home/ubuntu/.nddevice/latest
# Output: lrwxrwxrwx 1 root root ... latest -> 6.14.0.rc.12345
# Extract version: 6.14
```

For krait:
```bash
ls -lrth /data/nd_files/.nddevice/latest
```

**Pass/Fail:** PASS if version >= 6.10, FAIL otherwise.

### check_audio_files

Validate audio files on device against a reference CSV.

**Parameters:**
- `file_name` — CSV filename (e.g., `D450_audio.csv`, `D210_D215_IN_audio.csv`)

CSV location: `claude_device_validator/assets/audio/<file_name>`

**CSV columns:** `file_path, md5, channels, bit_depth, sample_rate, duration, file_size`

**Implementation for each row:**
```bash
# 1. Check file exists
ls <file_path>

# 2. Check MD5
md5sum <file_path> | awk '{print $1}'
# Compare with expected md5

# 3. Check file size
stat -c %s <file_path>

# 4. Check audio metadata via ffprobe
ffprobe -v quiet -show_streams <file_path>
# Validate: channels, bits_per_sample, sample_rate, duration
```

**Pass/Fail:** PASS if ALL files match ALL criteria. FAIL on any mismatch.

### check_file_in_path

Check if files from a list exist in a specified path on device.

**Parameters:**
- `file_list_string` — newline-separated list of filenames
- `path` — directory path on device (default: `/media/data/nd_sdcard`)
- `inverse` — if `true`, expect files NOT to be found

**Implementation:**
```bash
# For each filename in list:
ls <path>/<filename>* 2>/dev/null

# Also check for .ld.mp4 files (should NOT exist = fail)
# Also check for duplicates in the list
```

**Pass/Fail:**
- Normal mode: PASS if all files found
- Inverse mode: PASS if NO files found (e.g., verifying files were deleted/not copied)
- FAIL if any `.ld.mp4` files are in the list

## File Paths by Device Type

| Item | Bagheera/Octo | Krait |
|------|---------------|-------|
| iriscli files | `/home/iriscli/files/` | `/data/nd_files/iriscli/files/` |
| SD card | `/media/data/nd_sdcard/` | `/media/data/nd_sdcard/` |
| OTA symlink | `/home/ubuntu/.nddevice/latest` | `/data/nd_files/.nddevice/latest` |
| Config root | `/home/ubuntu/config/` | `/data/nd_files/config/` |
| Audio assets | `/home/ubuntu/.nddevice/latest/audio/` | `/data/nd_files/.nddevice/latest/audio/` |
