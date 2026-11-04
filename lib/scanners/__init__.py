"""Day scanners: one module per requirement, each reading the same day of logs (in RAM).

lib/day_scan.py unpacks a device-day once and calls every scanner in SCANNERS on it.
A scanner module has:
  STEP          step name for the timings / log ('grep_tests', ...)
  scan(ctx)     -> dict of fields added to the day's result (see day_scan.DayContext)
Adding a requirement: write lib/scanners/<name>.py and add it to SCANNERS; the device is
put together from the day results in ota_report.finish_device.
"""

from scanners import (config_parser, drive, api, kpi, log_files, ota_version, override_evidence,
                      test_steps)

# Run in this order on every device-day
SCANNERS = [config_parser, ota_version, test_steps, drive, api, kpi, override_evidence, log_files]
