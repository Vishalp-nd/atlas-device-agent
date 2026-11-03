"""Drive and power monitor events (lib/drive_metrics.py, lib/DRIVE_METRICS_LOGIC.md)."""

import drive_metrics

STEP = 'drive_scan'


def scan(ctx):
    events = drive_metrics.DriveEvents()
    events.add(drive_metrics.scan_day(ctx.logs_dir))
    return {'events': events}
