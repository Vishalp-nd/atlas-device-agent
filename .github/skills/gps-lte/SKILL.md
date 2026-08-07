---
name: gps-lte
description: "Use when: enabling/disabling GPS or LTE on device, checking internet connectivity on device, checking eth1/wwan0 network interface status, toggling modem via GPIO. Ported from nd_test_bot (6.14_changes branch) gps_lte_api.py — all device-type subclasses."
argument-hint: "action (e.g., set_gps on, set_lte off, check_internet_exist, check_eth1_status)"
---

# GPS / LTE Controller

Enable/disable GPS and LTE modem via GPIO pins, check internet connectivity, and verify network interface status. Platform-aware with device-type-specific implementations.

Source: Ported from nd_test_bot repo, branch 6.14_changes, file `Test_Automation_Framework/Lib/apis/gps_lte_api.py`.

## Supported Actions

### set_gps / set_lte

Toggle GPS/LTE modem on or off. `set_lte` delegates to `set_gps` (same modem).

**Device-specific implementations:**

| Device Type | GPIO Command | Off Value | On Value | USB Grep |
|-------------|-------------|-----------|----------|----------|
| octo | `/bin/vendor/gpio_test -n 228 -o {val}` | 0 | 1 | `lsusb \| grep 2c7c` |
| bagheera2 | `/bin/vendor/gpio_test -n 389 -o {val}` | 0 | 1 | `lsusb \| grep 1199` |
| bagheera3 | `/bin/vendor/gpio_test -n 228 -o {val}` | **1** (inverted) | **0** (inverted) | `lsusb \| grep 1199` |
| krait | Upload+run `gps_lte.py` | **1** (inverted) | **0** (inverted) | N/A |
| krait2 | Upload+run `gps_lte_krait2.py` | **1** (inverted) | **0** (inverted) | N/A |

**Turning OFF (all device types):**
```bash
# 1. Toggle GPIO to disable modem
sudo /bin/vendor/gpio_test -n <PIN> -o <OFF_VALUE>

# 2. Kill connection manager
sudo systemctl stop conn_mgr

# 3. Verify modem is gone
lsusb | grep <VENDOR_ID>  # should return empty
```

**Turning ON (all device types):**
```bash
# 1. Toggle GPIO to enable modem
sudo /bin/vendor/gpio_test -n <PIN> -o <ON_VALUE>

# 2. Wait for modem to appear
sleep 10
lsusb | grep <VENDOR_ID>  # should find device

# 3. Start connection manager
sudo systemctl start conn_mgr
```

**Krait-specific:** Uses Python script instead of direct GPIO:
```bash
# Upload gps_lte.py to device
adb push gps_lte.py /home/ubuntu/
# Run: 1=off, 0=on (inverted!)
adb shell "python3 /home/ubuntu/gps_lte.py <VAL>"
# Cleanup
adb shell "rm /home/ubuntu/gps_lte.py"
```

### check_internet_exist

Check if device has internet connectivity by pinging DNS servers.

**Implementation by device type:**

| Device Type | Command |
|-------------|---------|
| bagheera / bagheera2 / bagheera3 / octo | `ping -I eth1 -c 3 8.8.8.8` |
| krait | `busybox ping6 -I eth0 -c 3 2001:4860:4860::8888` |
| krait2 | `busybox ping6 -I eth0 -c 3 2001:4860:4860::8888` |

**Pass/Fail:** PASS if ping succeeds (0% packet loss), FAIL if 100% loss or timeout.

### check_eth1_status

Poll the network interface until it shows "RUNNING" state.

**Implementation:**
```bash
# Poll up to 60 times, 5 seconds apart
ifconfig eth1  # bagheera/octo
ifconfig eth0  # krait
ifconfig wwan0 # fallback

# Look for "RUNNING" in output
# PASS when found, FAIL after 300s timeout
```

| Device Type | Interface |
|-------------|-----------|
| bagheera / bagheera2 / bagheera3 / octo | `eth1` |
| krait / krait2 | `eth0` |
| Fallback | `wwan0` |

## Notes

- **CRITICAL:** bagheera3 and krait/krait2 have INVERTED GPIO logic (1=off, 0=on)
- After toggling GPS/LTE, wait at least 10 seconds for modem to initialize
- `conn_mgr` service manages the LTE connection — must be stopped/started with modem
- After turning LTE back on, use `check_eth1_status` to confirm interface is RUNNING before proceeding
