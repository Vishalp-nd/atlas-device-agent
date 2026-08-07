---
name: file-operations
description: "Use when: checking file generation on device (iriscli/files, ND_INPUT, SD card), verifying file permissions, checking file encryption via ffprobe, getting epoch timestamps from filenames, counting files in a path, validating file sizes, checking video upload status, or resolving device-type-specific remote file paths. Ported from nd_test_bot (6.14_changes branch) files_api.py, calculator_api.py, cmd_dict.py."
argument-hint: "action and params (e.g., check_file_generation camera, check_sd_card epoch cam_list, get_epoch earliest, check_permissions, check_encryption epoch, count_files path max duration)"
---

# File Operations

Device file system operations: check file generation, verify permissions, test encryption, get epochs from filenames, count files, validate sizes, check uploads, and resolve platform-specific paths.

**Source**: Ported from `nd_test_bot` repo, branch `6.14_changes`:
- `files_api.py` — file generation, epochs, SD card, counting, sizes
- `calculator_api.py` — permissions, encryption, video upload, size validation
- `cmd_dict.py` — remote filepath resolution

## Platform-Specific Paths

| Key | krait/krait2 | bagheera2/bagheera3 | octo |
|-----|-------------|---------------------|------|
| `sd_card_path` | `/data/nd_files/nd_sdcard` | `/media/data/nd_sdcard` | `/media/data/nd_sdcard` |
| `files_path` | `/data/nd_files/iriscli/files` | `/home/iriscli/files` | `/home/iriscli/files` |
| `ND_INPUT_PATH` | `/data/nd_files/iriscli/ND_INPUT` | `/home/iriscli/ND_INPUT` | `/home/iriscli/ND_INPUT` |
| `device_path` | `/home/ubuntu/.nddevice` | `/home/ubuntu/.nddevice` | `/home/ubuntu/.nddevice` |
| `bagheera_override` | `/data/nd_files/config/bagheera_override.ini` | `/home/ubuntu/config/bagheera_override.ini` | `/home/ubuntu/config/bagheera_override.ini` |
| `observations` | `/home/ubuntu/.nddevice/observations` | `/home/ubuntu/.nddevice/observations` | `/home/ubuntu/.nddevice/observations` |

### Camera Numbers by Device Type

| Device Type | Cameras |
|-------------|---------|
| krait/krait2 | 0 (outward), 1 (inward) |
| bagheera2/bagheera3 | 0 (outward), 1 (inward), 2 (side left), 3 (side right) |
| octo | 0 (outward), 1 (inward), 2-7 (additional cams) |

## Procedures

### get_remote_filepath — Resolve device-type-specific path

Pure lookup — no device command. Use the path mapping table above. Example:
```
key=sd_card_path → krait: /data/nd_files/nd_sdcard, bagheera: /media/data/nd_sdcard
key=FILES_FOLDER_PATH → krait: /data/nd_files/iriscli/files, bagheera: /home/iriscli/files
```

Used by: TC-111, TC-170-173, TC-98, TC-306, TC-307, TC-1471, TC-1682, TC-1684, TC-1710, TC-1717, TC-1919, TC-1920, TC-1806, TC-343

### get_epoch_from_file — Get earliest/latest epoch from filenames

```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls /home/iriscli/files"
# Or for krait:
adb -s <ADB_SERIAL> shell "ls /data/nd_files/iriscli/files"
```

Parse `.mp4` filenames: format is `{cam}_{...}_{epoch}_y.mp4`. Extract epoch (second-to-last `_`-delimited field). Return earliest or latest.

Used by: TC-111, TC-254, TC-255, TC-89, TC-1684, TC-1710

### get_epochs_from_path — Get epoch list from a directory

```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls -lt <PATH>/ | cut -d_ -f7 | sed -n '1!p'"
```

Returns sorted list of unique epochs from filenames in the given path.

Used by: TC-98

### check_epoch_in_ND_Input — Verify epoch file exists in ND_INPUT

```bash
# Poll up to 60 iterations (2s apart) for .STATE file:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <ND_INPUT_PATH>"
# Check for: 0_*<EPOCH>_y.STATE in the listing
```

Used by: TC-254

### check_file_generation — Verify files exist for cameras

```bash
# Check camera files in iriscli/files:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <FILES_PATH>/<CAM>_*_y.mp4"
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <FILES_PATH>/<CAM>_*_y.mp4.ld.mp4"
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <FILES_PATH>/0_*_y_partial.csv"

# For ND_INPUT type:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <ND_INPUT_PATH>/0_*<EPOCH>_y.STATE"
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <ND_INPUT_PATH>/0_*_<EPOCH>_ymetadata.txt"

# For audio type:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <FILES_PATH>/0_*.pcm"
```

**Privacy mode**: When inward privacy is active, cam 1 files should NOT exist.

Used by: TC-254, TC-89, TC-99

### check_file_generation_in_files — Verify standard files in iriscli/files

```bash
# Check all expected file types:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <FILES_PATH>/0_*_y.mp4"
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <FILES_PATH>/1_*_y.mp4"
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <FILES_PATH>/0_*_y.mp4.ld.mp4"
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <FILES_PATH>/1_*_y.mp4.ld.mp4"
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <FILES_PATH>/0_*_y_partial.csv"
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <FILES_PATH>/0_*_y_partial.pcm"
# bagheera2/3 also check cam 2 and 3
```

Used by: TC-89

### check_file_generation_in_ND_INPUT — Verify files in ND_INPUT for epoch

```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <ND_INPUT_PATH>/0_*<EPOCH>_y.STATE"
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <ND_INPUT_PATH>/0_*_<EPOCH>_ymetadata.txt"
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <ND_INPUT_PATH>/0_*0_*_<EPOCH>_y.chm.*"
```

Used by: TC-254

### check_Sd_card_file_generation — Verify files on SD card

Wait 100s, then check:
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls <SD_CARD_PATH>/<CAM>_*<EPOCH>_y.mp4"
```

For `cam_num_present`: files MUST exist. For `cam_num_not_present`: files must NOT exist.

Used by: TC-111, TC-1416, TC-1470, TC-1471, TC-1524, TC-1682, TC-1710, TC-1717, TC-1919, TC-1920

### check_file_permissions — Verify file permissions

```bash
# Generic (caller supplies ls command):
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls -l <FILES_PATH>/<CAM>_*_y.mp4.ld.mp4 | awk '{print \$1}'"
```

Check all returned permissions are identical. Expected: varies by device type.

Used by: TC-169, TC-99

### check_file_permissions_octo — Verify octo file permissions

For cameras 2+ (not 0,1):
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls -l /home/iriscli/files/<CAM>_*_y.mp4 | awk '{print \$1}' | cut -d'.' -f1 | grep -v total"
```

Expected: `-rw-r--r--` for all files.

Used by: TC-99

### check_file_encryption_octo — Verify video encryption via ffprobe

```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ffprobe <SD_CARD_PATH>/<CAM>_*<EPOCH>_y.mp4 2>&1 | grep -q 'moov atom not found' && echo 'True' || echo 'False'"
# For cam 0,1 also check .mp4.ld.mp4:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ffprobe <SD_CARD_PATH>/<CAM>_*<EPOCH>_y.mp4.ld.mp4 2>&1 | grep -q 'moov atom not found' && echo 'True' || echo 'False'"
```

**PASS**: "moov atom not found" → files ARE encrypted. **FAIL**: ffprobe can read them → NOT encrypted.

Used by: TC-111

### check_file_sizes_octo — Verify file sizes on SD card

```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls -l <SD_CARD_PATH>/<CAM>_*_<EPOCH>_y.mp4 | awk '{print \$5}'"
# For cam 0,1 also check .mp4.ld.mp4:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls -l <SD_CARD_PATH>/<CAM>_*_<EPOCH>_y.mp4.ld.mp4 | awk '{print \$5}'"
```

For cams other than 0,1: **FAIL** if any file > 25MB (26214400 bytes).

Used by: TC-98, TC-1416, TC-1470, TC-1471, TC-1682, TC-1717

### validate_size_range — Check size within range

Pure arithmetic check — no device command:
```
if min_size <= size <= max_size: PASS
else: FAIL
```

Used by: TC-138, TC-139, TC-169, TC-170-173, TC-96, TC-98, TC-1683

### count_files_in_path — Monitor file count over time

```bash
# Check every 5s for <DURATION> seconds:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ls -l <PATH> | wc -l"
```

**PASS**: Count never exceeds `maxsize`. **FAIL**: Count exceeds `maxsize` at any check.

Used by: TC-306, TC-307

### check_video_file_uploaded_octo — Verify video upload

```bash
# Retry up to 10 times with 60s interval:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "grep -ria 'Upload successful for video: /media/data/nd_sdcard/<CAM>_trip.*<EPOCH>_y.mp4' /home/ubuntu/.nddevice/log/unifieduploader/*"
```

**PASS**: Upload log found. **FAIL**: Not found after 10 retries.

Used by: TC-1470, TC-1524
