"""
Read a downloaded device-day straight from its archives.

The downloader saves each day as OUTPUT/log/<device>/<day>/*.7z|*.zip and, unless
run with --no-extract, merges them into <day>/logs/<service>.log (extractor.mergelog).
The archives are ~17x smaller than the merged logs, so on slow disks it is much
faster to rebuild logs/ in RAM (/dev/shm) than to read the extracted copy.

  with day_logs(day_dir) as logs_dir:
      ...  # logs_dir looks exactly like <day>/logs

merge_archives() builds the same logs/ as extractor.mergelog, only faster: the
7z CLI extracts (mergelog goes through py7zr twice per archive; py7zr is still
used when there is no 7z binary, e.g. in the slim Docker image) and pieces are
copied as bytes with mergelog's text-mode newline translation applied. Syslog
tarballs are skipped; mergelog unpacks them outside logs/.

The rebuilt day lives in LOG_ARCHIVE_TMP if set, else /dev/shm when it has at
least SHM_MIN_BYTES (Docker's default /dev/shm is far too small), else the
system temp folder.
"""

import contextlib
import errno
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import py7zr

from run_logging import timed

log = logging.getLogger('log_archive')

ARCHIVE_SUFFIXES = ('.7z', '.zip')
SHM_MIN_BYTES = 8 * 1024 ** 3
SEVEN_ZIP = shutil.which('7z') or shutil.which('7za')


def _tmp_root():
    if os.environ.get('LOG_ARCHIVE_TMP'):
        return os.environ['LOG_ARCHIVE_TMP']
    try:
        if shutil.disk_usage('/dev/shm').total >= SHM_MIN_BYTES:
            return '/dev/shm'
    except OSError:
        pass
    return None  # tempfile's default


TMP_ROOT = _tmp_root()


def archives(day_dir):
    return sorted(p for p in Path(day_dir).iterdir() if p.is_file() and p.name.endswith(ARCHIVE_SUFFIXES))


def unpacked_bytes(path):
    """Bytes an archive unpacks to, read from its header (an upper bound for what
    merge_archives writes from it), or None if the header can't be read."""
    path = Path(path)
    try:
        if '.zip' in path.name:
            with zipfile.ZipFile(path) as z:
                return sum(i.file_size for i in z.infolist())
        with py7zr.SevenZipFile(path) as z:
            return z.archiveinfo().uncompressed
    except Exception:
        return None


def has_logs(day_dir):
    """True if the day has archives or an extracted logs/ folder."""
    day_dir = Path(day_dir)
    return day_dir.is_dir() and ((day_dir / 'logs').is_dir() or bool(archives(day_dir)))


def _text_mode_copy(data):
    """What mergelog's text-mode read + write does to a piece: universal newlines.
    (Its iso8859-15 decode/encode round-trips every byte unchanged.)"""
    return data.replace(b'\r\n', b'\n').replace(b'\r', b'\n')


def _archive_members(path, work):
    """[(name, bytes)] of an archive sorted by name, or None if it is corrupt."""
    if '.zip' in path.name:
        try:
            with zipfile.ZipFile(path) as z:
                return [(name, z.read(name)) for name in sorted(z.namelist())]
        except zipfile.BadZipFile:
            return None
    out = Path(work) / 'x'
    shutil.rmtree(out, ignore_errors=True)
    if SEVEN_ZIP:
        result = subprocess.run([SEVEN_ZIP, 'x', '-y', '-bd', f'-o{out}', str(path)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode != 0:
            # A full temp folder is not a corrupt archive: raise, so the day is retried
            if b'space' in result.stderr.lower() or _no_space(work):
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), str(path))
            return None
    else:
        try:
            with py7zr.SevenZipFile(path) as z:
                z.extractall(out)
        except py7zr.exceptions.Bad7zFile:
            return None
    members = []
    for root, _, files in os.walk(out):
        for fname in files:
            full = os.path.join(root, fname)
            with open(full, 'rb') as f:
                members.append((os.path.relpath(full, out), f.read()))
    members.sort()
    return members


def _no_space(folder, slack=64 * 1024 ** 2):
    try:
        return shutil.disk_usage(folder).free < slack
    except OSError:
        return False


def merge_archives(day_archives, logs_dir, work):
    """Build logs_dir from a day's archives exactly like extractor.Extract.mergelog."""
    os.makedirs(logs_dir, exist_ok=True)
    for path in day_archives:
        # mergelog only takes files whose content is a 7z/zip, and skips .tmp downloads
        if not (py7zr.is_7zfile(path) or zipfile.is_zipfile(path)) or '.tmp' in str(path):
            continue
        members = _archive_members(path, work)
        if members is None:
            # mergelog wipes what it merged so far and carries on with the next archive
            log.warning('%s may be corrupted, dropping what was merged so far (as mergelog does)', path)
            shutil.rmtree(logs_dir)
            os.makedirs(logs_dir)
            continue
        stem = path.name.split('.')[0]
        for name, data in members:
            partial = f'{stem}/{name}'
            if 'trace_' in partial:
                continue
            if 'syslog_monitor' in partial and partial.endswith('.log'):
                os.makedirs(os.path.join(logs_dir, 'syslogs'), exist_ok=True)
                dest = os.path.join(logs_dir, 'syslogs', os.path.basename(partial))
            elif 'syslog' in partial:
                continue  # tarball mergelog unpacks outside logs/
            else:
                dest = os.path.join(logs_dir, name.split('/')[0] + '.log')
                if '.log.log' in dest:
                    dest = dest[:-4]
            with open(dest, 'ab') as f:
                f.write(_text_mode_copy(data))
                f.write(b'\n')


@contextlib.contextmanager
def day_logs(day_dir, prefer_archives=True, timings=None):
    """Yield a logs/ directory for a downloaded day.

    Built in RAM from the day's archives (removed afterwards) when there are any
    and prefer_archives is set, else the extracted <day>/logs. The rebuild is
    timed (step 'rebuild_day', appended to timings if given).
    """
    day_dir = Path(day_dir)
    found = archives(day_dir) if day_dir.is_dir() else []
    if not found or (not prefer_archives and (day_dir / 'logs').is_dir()):
        yield day_dir / 'logs'
        return
    tmp = tempfile.mkdtemp(prefix='daylogs_', dir=TMP_ROOT)
    try:
        with timed(log, 'rebuild_day', timings, level=logging.DEBUG,
                   device=day_dir.parent.name, day=day_dir.name, archives=len(found)):
            merge_archives(found, os.path.join(tmp, 'logs'), tmp)
        yield Path(tmp) / 'logs'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
