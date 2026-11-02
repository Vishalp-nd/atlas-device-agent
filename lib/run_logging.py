"""
Logging with step timings, shared by the boot-wise report pipeline
(lib/ota_report.py, lib/cfg_parser_logs.py, lib/log_archive.py) and the Log
Analyzer service (log_analyzer/).

Modules only call logging.getLogger(...) and timed(...); the process entry point
(a CLI main, or the Log Analyzer worker) calls configure() once. ProcessPool
workers are forked, so they inherit the configured handlers.

  with timed(log, 'grep', timings, device=device, day=day):
      ...
  # -> "grep device=103182502310 day=2026-10-20 took 6.21s", and timings gets
  #    {'step': 'grep', 'device': ..., 'day': ..., 'started_at': ..., 'duration_s': 6.21}
"""

import contextlib
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOG_FORMAT = '%(asctime)s | %(levelname)s | %(processName)s | %(name)s | %(message)s'


def configure(log_files=(), level=logging.INFO, stream=True):
    """Send every logger's records to stdout and/or the given files (appended)."""
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT)
    handlers = []
    if stream:
        handlers.append(logging.StreamHandler(sys.stdout))
    for path in log_files:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Plain FileHandler (no rotation): several worker processes append to the
        # same files, and O_APPEND keeps whole lines intact
        handlers.append(logging.FileHandler(path, encoding='utf-8'))
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    return handlers


def _label(step, fields):
    extra = ' '.join(f'{k}={v}' for k, v in fields.items() if v is not None)
    return f'{step} {extra}' if extra else step


@contextlib.contextmanager
def timed(logger, step, timings=None, level=logging.INFO, **fields):
    """Log how long the block takes (and failures), optionally appending to timings."""
    label = _label(step, fields)
    started_at = datetime.now(timezone.utc)
    start = time.perf_counter()
    logger.debug('%s started', label)
    status = 'ok'
    try:
        yield
    except BaseException as exc:
        status = 'failed'
        logger.error('%s failed after %.2fs: %s', label, time.perf_counter() - start, exc)
        raise
    finally:
        duration = round(time.perf_counter() - start, 3)
        if status == 'ok':
            logger.log(level, '%s took %.2fs', label, duration)
        if timings is not None:
            timings.append({'step': step, **fields, 'status': status,
                            'started_at': started_at.isoformat(), 'duration_s': duration})
