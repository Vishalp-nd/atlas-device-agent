"""Test case log steps: every line matching a search_log pattern of the device-validator
tests (lib/testcase_log_steps.py), spooled to a gzip file per day. Boots are only known
once every day is read, so ota_report.load_hits assigns the hits to boots afterwards."""

import gzip
import os
import tempfile

import log_archive
from log_search import iter_service_hits, service_files

STEP = 'grep_tests'


def spool_day_hits(logs_dir, spool, patterns_by_service):
    """Write the day's test-pattern hits to spool as 'service\\tts\\tidx,idx\\tline' records.

    Returns the services that had log files.
    """
    services = set()
    for service, patterns in patterns_by_service.items():
        files = service_files([logs_dir], service)
        if not files:
            continue
        services.add(service)
        for ts, matched, line in iter_service_hits(files, patterns):
            spool.write(f'{service}\t{ts}\t{",".join(map(str, matched))}\t{line[:400]}\n')
    return services


def scan(ctx):
    """{'spool': gzip file of the hits (the caller deletes it), 'services': services with logs}."""
    spool_file = tempfile.NamedTemporaryFile(prefix=f'hits_{ctx.device}_{ctx.day}_', suffix='.gz',
                                             dir=log_archive.TMP_ROOT, delete=False)
    spool_file.close()
    ctx.on_failure(lambda: os.path.exists(spool_file.name) and os.unlink(spool_file.name))
    with gzip.open(spool_file.name, 'wt', encoding='latin-1', newline='\n', compresslevel=1) as spool:
        services = spool_day_hits(ctx.logs_dir, spool, ctx.settings['patterns'])
    return {'spool': spool_file.name, 'services': services}
