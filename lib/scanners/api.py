"""Cloud API calls (lib/api_calls.py)."""

import api_calls
from log_search import iter_service_hits

STEP = 'api_grep'


def scan(ctx):
    return {'api_calls': api_calls.scan_day(ctx.logs_dir, iter_service_hits)}
