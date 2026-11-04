"""KPI Summary lines (lib/kpi_metrics.py: KPI_LOG_LINES), one grep pass per service; the
KPIs are computed per boot in ota_report.finish_device once the device's boots are known."""

import kpi_metrics
from log_search import iter_service_hits

STEP = 'kpi_grep'


def scan(ctx):
    return {'kpi_hits': kpi_metrics.scan_day(ctx.logs_dir, iter_service_hits)}
