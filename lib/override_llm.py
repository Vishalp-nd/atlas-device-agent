"""
Overridden config values per boot, read from the boot's own log lines by an LLM.

CFG_PRSR only lists the keys that are *not* overridden, so for every feature key
it does not list (feature_status), the services' own startup lines are the only
place the effective value shows up, in many formats:
    apm_wom_enable                  : 0
    Privacy configuration loaded - save_user_alert_video=0, off_duty_mode=1, upload_video[outward=1, ...]
    save_usr_alert_video:0, inward_led_color:red
Those lines (EvidenceCollector: `grep -E` per day, then the first EVIDENCE_WINDOW_MS
of the boot) go to the Azure OpenAI deployment in OVERRIDE_LLM_MODEL (gpt-5.4-mini at
reasoning effort OVERRIDE_LLM_REASONING=low; OAI_MODEL when unset) through LangChain, and
JsonOutputParser turns the answer into
    {"section.key": {"value": ..., "evidence": "<line>"} | null}.

Answers are cached on disk by prompt hash: prompts carry no timestamps or pids,
so boots that log the same configuration share one call. The model and reasoning effort
are part of the key, so changing either asks every boot again.

Model choice: on 10 staging boots (445 keys) gpt-5.4-mini at low reasoning gave the same
values as gpt-5.4-2 in about half the time (~4 s vs ~8 s a boot). Without reasoning it reads
upload_video[inward=0] as privacy_mode.inward; o3-mini / gpt-4.1(-mini) / gpt-4o-mini were
slower or missed values. The other LLM calls (report_callouts) stay on OAI_MODEL.

When the endpoint can't be reached (connection error or timeout), a marker file next to the
cache (UNREACHABLE_MARK) tells every worker process to skip the LLM for OVERRIDE_LLM_COOLDOWN_S
(300 s): the boots fall back to defaults/overrides instead of each retrying for minutes (a run
once spent 21 hours that way). After the cooldown one call tries again; a success clears it.

Configuration (env or .env at the repo root): AZURE_OAI, OAI_ENDPOINT, OAI_MODEL,
OVERRIDE_LLM_MODEL (deployment for this step; OAI_MODEL if empty), OVERRIDE_LLM_REASONING
(reasoning_effort: none/low/medium/high; empty: the model default), OVERRIDE_LLM_API_VERSION
(default 2025-04-01-preview). OVERRIDE_LLM=0 turns the calls off; missing credentials do too.
"""

import bisect
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import cfg_parser_logs

log = logging.getLogger('override_llm')

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get('OVERRIDE_LLM_CACHE', REPO_ROOT / 'OUTPUT' / 'llm_cache' / 'override_values'))
PROMPT_VERSION = 'v1'

# Startup configuration is logged in the first minutes of a boot
EVIDENCE_WINDOW_MS = 10 * 60 * 1000
MAX_EVIDENCE_LINES = 150
MAX_EVIDENCE_CHARS = 16000
MAX_LINE_CHARS = 300

# ts: uptime: TAG: L: pid: tid: message
PREFIX_RE = re.compile(r'^\d+:\s*\d+:\s*\w+:\s*[A-Z]:\s*\d+:\s*\d+:\s*')
DIGITS_RE = re.compile(r'\d')


def _generic(key):
    """Plain words (audio, inward, left, ...) also appear in ordinary sentences."""
    return '_' not in key and not re.search(r'[a-z][A-Z]', key) and len(key) < 12


class EvidenceCollector:
    """Lines that print a feature key with a value, gathered over every day of a device."""

    def __init__(self, keys):
        self.keys = sorted(set(keys))
        self.patterns = [rf'{re.escape(k)}[[:space:]]*[=:]' for k in self.keys]
        self.key_res = {k: re.compile(rf'(?<![\w.]){re.escape(k)}\s*[=:]', re.IGNORECASE) for k in self.keys}
        self.generic = {k for k in self.keys if _generic(k)}
        self.lines = {}  # (service, normalized message) -> [(ts, message, keys)]

    def scan_day(self, logs_dir, line_timestamp):
        if not self.keys:
            return
        files = sorted(p for p in logs_dir.rglob('*') if p.is_file())
        args = ['-E', '-i', *sum((['-e', p] for p in self.patterns), [])]
        for path, line in cfg_parser_logs.grep_lines(args, files):
            ts = line_timestamp(line)
            if ts is None:
                continue
            m = PREFIX_RE.match(line)
            message = line[m.end():] if m else line
            keys = {k for k, rx in self.key_res.items() if rx.search(message)}
            specific = keys - self.generic
            # A plain word only counts next to other key=value pairs (a config dump)
            if not specific and len(keys) < 2:
                continue
            service = Path(path).name.split('.')[0]
            message = message[:MAX_LINE_CHARS]
            norm = (service, DIGITS_RE.sub('0', message))
            self.lines.setdefault(norm, []).append((ts, message, frozenset(keys)))

    def finish(self):
        self.by_time = sorted((ts, service, message, keys)
                              for (service, _), hits in self.lines.items() for ts, message, keys in hits)
        self.times = [h[0] for h in self.by_time]

    def boot_evidence(self, start, wanted_keys):
        """Distinct lines mentioning wanted keys in the first EVIDENCE_WINDOW_MS of a boot."""
        if start is None or not wanted_keys:
            return []
        lo = bisect.bisect_left(self.times, start)
        hi = bisect.bisect_left(self.times, start + EVIDENCE_WINDOW_MS)
        out, seen, chars = [], set(), 0
        for ts, service, message, keys in self.by_time[lo:hi]:
            if not keys & wanted_keys:
                continue
            item = f'{service} | {message}'
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
            chars += len(item)
            if len(out) >= MAX_EVIDENCE_LINES or chars >= MAX_EVIDENCE_CHARS:
                break
        return out


# ── LLM ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You read startup log lines of an in-vehicle camera device and report the effective
value of configuration keys. Config keys are given as section.key with the OTA default value.

Rules:
- Use only the log lines. A key's value counts when a line clearly prints that key (or an obvious
  spelling variant, e.g. save_usr_alert_video for save_user_alert_video) with a value.
- Keys with the same name live in different sections (privacy_mode.inward vs upload_video.inward);
  use the line's context (e.g. "upload_video[...]") to tell them apart. If a line does not say which
  section it is, only use it when just one requested section has that key.
- Report values as logged; booleans may be logged as 0/1 or true/false.
- If no line gives the value, use null. Never guess from the default.

Return only JSON of this shape, one entry per requested key:
{{"<section.key>": {{"value": <logged value>, "evidence": "<the log line>"}} or null}}"""

HUMAN_PROMPT = """Config keys (section.key = OTA default):
{keys}

Log lines (service | message), first minutes of one boot:
{lines}"""

_chain = None
_chain_lock = threading.Lock()

# Shared by the run's worker processes (and runs): its mtime is when the endpoint last failed to answer
UNREACHABLE_MARK = CACHE_DIR / '.llm_unreachable'
COOLDOWN_S = float(os.environ.get('OVERRIDE_LLM_COOLDOWN_S', '300'))


class LLMUnavailable(RuntimeError):
    """The endpoint failed to answer within the cooldown: not called."""


def _is_connection_error(exc):
    """A connection error or timeout (openai.APIConnectionError covers APITimeoutError)."""
    try:
        import openai
        if isinstance(exc, openai.APIConnectionError):
            return True
    except ImportError:
        pass
    return isinstance(exc, (ConnectionError, TimeoutError))


def _check_reachable():
    """Raise LLMUnavailable while the endpoint is in its cooldown. When the cooldown is over,
    this call becomes the probe: the marker is refreshed first so the other workers keep
    skipping until the probe answers (and clears it) or fails (and restarts the cooldown)."""
    try:
        age = time.time() - UNREACHABLE_MARK.stat().st_mtime
    except OSError:
        return
    if age < COOLDOWN_S:
        raise LLMUnavailable(f'LLM endpoint unreachable ({age:.0f}s ago); not called, tried again after {COOLDOWN_S:.0f}s')
    try:
        os.utime(UNREACHABLE_MARK)
    except OSError:
        pass


def _mark(unreachable, exc=None):
    try:
        if unreachable:
            UNREACHABLE_MARK.parent.mkdir(parents=True, exist_ok=True)
            UNREACHABLE_MARK.write_text(f'{type(exc).__name__}: {exc}'[:300])
            log.warning('LLM endpoint unreachable (%s): skipping LLM calls for %.0fs', type(exc).__name__, COOLDOWN_S)
        elif UNREACHABLE_MARK.exists():
            UNREACHABLE_MARK.unlink(missing_ok=True)
            log.info('LLM endpoint reachable again')
    except OSError:
        pass


def enabled():
    if os.environ.get('OVERRIDE_LLM', '1').strip().lower() in ('0', 'false', 'no', 'off'):
        return False
    _load_env()
    return all(os.environ.get(k, '').strip() for k in ('AZURE_OAI', 'OAI_ENDPOINT', 'OAI_MODEL'))


def _load_env():
    if os.environ.get('AZURE_OAI'):
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / '.env')
    except ImportError:
        pass


def model_settings():
    """(deployment, reasoning effort or None, api version) of the override LLM."""
    _load_env()
    model = os.environ.get('OVERRIDE_LLM_MODEL', '').strip() or os.environ.get('OAI_MODEL', '').strip()
    reasoning = os.environ.get('OVERRIDE_LLM_REASONING', '').strip().lower() or None
    api_version = os.environ.get('OVERRIDE_LLM_API_VERSION', '').strip() or '2025-04-01-preview'
    return model, reasoning, api_version


def _get_chain():
    global _chain
    with _chain_lock:
        if _chain is None:
            from langchain_core.output_parsers import JsonOutputParser
            from langchain_core.prompts import ChatPromptTemplate
            from langchain_openai import AzureChatOpenAI

            model, reasoning, api_version = model_settings()
            extra = {'reasoning_effort': reasoning} if reasoning else {}
            llm = AzureChatOpenAI(
                azure_deployment=model,
                azure_endpoint=os.environ['OAI_ENDPOINT'].strip(),
                api_key=os.environ['AZURE_OAI'].strip(),
                api_version=api_version,
                max_retries=int(os.environ.get('OVERRIDE_LLM_RETRIES', '6')),
                timeout=float(os.environ.get('OVERRIDE_LLM_TIMEOUT', '60')),
                **extra,
            )
            log.info('Override LLM: %s (reasoning %s, api %s)', model, reasoning or 'default', api_version)
            prompt = ChatPromptTemplate.from_messages([('system', SYSTEM_PROMPT), ('human', HUMAN_PROMPT)])
            _chain = prompt | llm | JsonOutputParser()
    return _chain


def _fmt_default(value):
    return 'not in the default config' if value is None else str(value)


def extract_values(conditions, evidence):
    """{section.key: {'value', 'evidence'} | None} for conditions [(section, key, default)].

    Returns (values, source) with source 'cache', 'llm' or 'no_evidence'.
    Raises on LLM errors (the caller decides how to degrade), LLMUnavailable without calling
    while the endpoint is in its unreachable cooldown.
    """
    wanted = sorted({(s, k, d) for s, k, d in conditions})
    if not evidence:
        return {f'{s}.{k}': None for s, k, _ in wanted}, 'no_evidence'
    variables = {
        'keys': '\n'.join(f'{s}.{k} = {_fmt_default(d)}' for s, k, d in wanted),
        'lines': '\n'.join(evidence),
    }
    model, reasoning, _ = model_settings()
    if reasoning:
        model = f'{model}@{reasoning}'
    digest = hashlib.sha256(json.dumps([PROMPT_VERSION, model, variables], sort_keys=True).encode()).hexdigest()
    cache_path = CACHE_DIR / digest[:2] / f'{digest}.json'
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text()), 'cache'
        except ValueError:
            pass
    _check_reachable()
    try:
        raw = _get_chain().invoke(variables)
    except Exception as exc:
        if _is_connection_error(exc):
            _mark(True, exc)
        raise
    _mark(False)
    values = {}
    for s, k, _ in wanted:
        name = f'{s}.{k}'
        entry = raw.get(name) if isinstance(raw, dict) else None
        if isinstance(entry, dict) and entry.get('value') is not None:
            values[name] = {'value': entry['value'], 'evidence': str(entry.get('evidence', ''))[:MAX_LINE_CHARS]}
        else:
            values[name] = None
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(values))
    tmp.replace(cache_path)
    return values, 'llm'
