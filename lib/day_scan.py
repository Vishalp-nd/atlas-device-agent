"""Read one device-day of logs once and run every scanner (lib/scanners/) on it.

The day is rebuilt in RAM from its archives (lib/log_archive.py) a single time; each
scanner then greps that copy for its own requirement and returns the fields it adds to
the day's result. Days are independent, so ota_report.run() reads them in parallel and
ota_report.finish_device() puts a device together once all its days are read.
"""

import logging

import log_archive
from run_logging import timed
from scanners import SCANNERS

# Same logger as before the scanners were split out of ota_report, so run logs are unchanged
log = logging.getLogger('ota_report')

# Shared by every scanner; set in the parent before the worker pool forks (configure)
_SETTINGS = {}


def configure(**settings):
    """patterns: {service: [(bre, regex)]} of the test steps; feature_keys: the feature
    config keys whose logged values are collected."""
    _SETTINGS.update(settings)


class DayContext:
    """What a scanner gets: the device, the day, its logs folder (in RAM) and the settings."""

    def __init__(self, device, day, logs_dir, settings):
        self.device, self.day, self.logs_dir, self.settings = device, day, logs_dir, settings
        self._on_failure = []

    def on_failure(self, fn):
        """Run fn if the day fails after this scanner (e.g. delete a temp file it made)."""
        self._on_failure.append(fn)


def scan_device_day(device, day, log_base_dir):
    """{'day', 'timings', **every scanner's fields} for one device-day."""
    timings = []
    result = {'day': day}
    ctx = None
    try:
        with timed(log, 'read_day', timings, device=device, day=day):
            with log_archive.day_logs(log_base_dir / device / day, timings=timings) as logs_dir:
                ctx = DayContext(device, day, logs_dir, _SETTINGS)
                for scanner in SCANNERS:
                    with timed(log, scanner.STEP, timings, level=logging.DEBUG, device=device, day=day):
                        result.update(scanner.scan(ctx))
    except BaseException:
        for fn in (ctx._on_failure if ctx else ()):
            fn()
        raise
    result['timings'] = timings
    return result
