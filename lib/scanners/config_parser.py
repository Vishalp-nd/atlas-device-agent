"""CFG_PRSR lines per service process, written to the day's CFG_PRSR json (cfg_parser_logs);
ota_report reads them back for the device's boots and config keys."""

import cfg_parser_logs

STEP = 'cfg_parse'


def scan(ctx):
    cfg_parser_logs.write_day(ctx.device, ctx.day, cfg_parser_logs.extract_day(ctx.logs_dir, 'CFG_PRSR'))
    return {}
