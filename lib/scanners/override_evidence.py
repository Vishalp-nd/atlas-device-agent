"""Lines where a service logs a feature config key's value (lib/override_llm.py), for the
LLM that reads the values CFG_PRSR does not show."""

import override_llm
from log_search import line_timestamp

STEP = 'evidence_grep'


def scan(ctx):
    evidence = override_llm.EvidenceCollector(ctx.settings['feature_keys'])
    evidence.scan_day(ctx.logs_dir, line_timestamp)
    return {'evidence': evidence.lines}
