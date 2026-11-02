"""
Statically extract the log checks (search_log calls) from the pytest
device-validator test cases, without running them.

For each tests/**/test_*.py file this returns the TC id, its feature and
functionality, and the ordered list of log steps. Each step is
  {"service": "ndcentral", "patterns": ["text {…}", ...], "greps": [...],
   "regexes": [...], "mode": "any" | "all", "expect": "present" | "absent" | "info",
   "line": 42, "method": "test_step3_..."}

Patterns follow search_log semantics on the device (grep -i, basic regex):
"greps" are the same basic regexes for grep on the downloaded logs, "regexes"
their Python translation (used to tell which pattern a grep hit belongs to).
Values only known at runtime (f-string fields, device.variables, ...) become
".*" / ".*?" (shown as {…} in "patterns"); steps whose service or pattern
can't be resolved at all are returned in "unresolved" instead.

Product line: "device_types" (on the test and on each step) lists the device types a
check applies to, from the tests' own device_type conditions: a precondition that
skips/returns for some types limits the whole test, an if/else around a search
limits that step, and an early `if <device_type cond>: return/skip` limits the
steps after it in the same method. None means every device type.

Feature: the "Feature: X — Y" docstring line (X = feature, Y = functionality),
else feature_name from the matching test_cases/<folder>/TC_*_<id>*.yaml, else
the feature most used by documented tests in the same folder, else the folder.
"""

import ast
import itertools
import json
import logging
import re
import warnings
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

log = logging.getLogger('testcase_log_steps')

DEFAULT_TESTS_DIR = Path('/home/ubuntu/device-automation/pytest_device_validator/tests')

SEARCH_METHODS = {'search_log', 'search_log_interval', 'search_logs_until_found'}
WILDCARD = object()
MAX_EXPANSIONS = 20
# A pattern needs this much literal text to be worth grepping for
MIN_LITERAL_CHARS = 4

FEATURE_RE = re.compile(r'^\s*Feature:\s*(.+?)\s*$', re.MULTILINE)
YAML_FEATURE_RE = re.compile(r"^feature_name:\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)
TC_ID_RE = re.compile(r'^\s*TC ID:\s*(\S+)', re.MULTILINE)
FEATURE_SPLIT_RE = re.compile(r'\s+[—–-]\s*')


def split_feature(text):
    """'SVC — Recovery Thread' -> ('SVC', 'Recovery Thread')."""
    parts = FEATURE_SPLIT_RE.split(text.strip(), maxsplit=1)
    feature = parts[0].strip()
    functionality = parts[1].strip() if len(parts) > 1 else ''
    return feature, functionality


def _tc_number(name):
    m = re.search(r'tc_[a-z0-9]*?_?(\d{2,6})(?:_|$)', name, re.IGNORECASE)
    if not m:
        m = re.search(r'_(\d{2,6})(?:_|$)', name)
    return m.group(1) if m else None


def _load_yaml_features(test_cases_dir):
    """(folder, tc_number) and tc_number -> feature_name from test_cases/**.yaml."""
    by_folder, by_number = {}, defaultdict(set)
    if not test_cases_dir.is_dir():
        return by_folder, {}
    for path in test_cases_dir.rglob('*.yaml'):
        m = YAML_FEATURE_RE.search(path.read_text(errors='replace'))
        number = _tc_number(path.stem)
        if not m or not number or '<' in m.group(1):
            continue
        by_folder[(path.parent.name.lower(), number)] = m.group(1)
        by_number[number].add(m.group(1))
    unique = {n: next(iter(f)) for n, f in by_number.items() if len(f) == 1}
    return by_folder, unique


# ── value resolution ────────────────────────────────────────────────────

def _scope_assigns(nodes):
    """name -> list of value nodes from simple assignments / for-loops in nodes."""
    scope = defaultdict(list)
    for node in nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign):
                for target in sub.targets:
                    if isinstance(target, ast.Name):
                        scope[target.id].append(sub.value)
                    elif (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                          and target.value.id in ('self', 'cls')):
                        scope['self.' + target.attr].append(sub.value)
            elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name) and sub.value:
                scope[sub.target.id].append(sub.value)
            elif isinstance(sub, (ast.For, ast.comprehension)) and isinstance(sub.target, ast.Name):
                scope[sub.target.id].append(('iter', sub.iter))
    return scope


def _resolve(node, scopes, depth=0):
    """Return a list of templates (lists of str / WILDCARD), or None."""
    if depth > 6 or node is None:
        return None
    if isinstance(node, tuple) and node[0] == 'iter':
        return _resolve_items(node[1], scopes, depth + 1)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [[node.value]]
    if isinstance(node, ast.JoinedStr):
        pieces = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                pieces.append([[str(value.value)]])
            else:
                inner = _resolve(value.value, scopes, depth + 1)
                pieces.append(inner if inner and len(inner) == 1 else [[WILDCARD]])
        return _combine(pieces)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve(node.left, scopes, depth + 1)
        right = _resolve(node.right, scopes, depth + 1)
        if left is None and right is None:
            return None
        return _combine([left or [[WILDCARD]], right or [[WILDCARD]]])
    name = None
    if isinstance(node, ast.Name):
        name = node.id
    elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in ('self', 'cls'):
        name = 'self.' + node.attr
    if name:
        for scope in scopes:
            if name in scope:
                values = scope[name]
                # Several assignments (e.g. per device type) -> try each
                results = []
                for value in values:
                    resolved = _resolve(value, scopes, depth + 1)
                    if resolved:
                        results.extend(resolved)
                return _dedupe(results) or None
        return None
    if isinstance(node, (ast.List, ast.Tuple)):
        return _resolve_items(node, scopes, depth)
    return None


def _resolve_items(node, scopes, depth):
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        results = []
        for elt in node.elts:
            resolved = _resolve(elt, scopes, depth + 1)
            if resolved:
                results.extend(resolved)
        return _dedupe(results) or None
    return _resolve(node, scopes, depth + 1)


def _combine(pieces):
    combos = itertools.islice(itertools.product(*pieces), MAX_EXPANSIONS)
    return [[seg for part in combo for seg in part] for combo in combos]


def _dedupe(templates):
    seen, out = set(), []
    for t in templates:
        key = tuple('\0' if s is WILDCARD else s for s in t)
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out[:MAX_EXPANSIONS]


POSIX_CLASSES = {
    'digit': r'0-9', 'alpha': r'a-zA-Z', 'alnum': r'a-zA-Z0-9', 'space': r'\s',
    'upper': r'A-Z', 'lower': r'a-z', 'xdigit': r'0-9a-fA-F', 'punct': r'!-/:-@\[-`{-~',
}


def bre_to_python(pattern):
    """Translate a GNU grep basic regex (what search_log runs on device) to Python re.

    In BRE `( ) | { } + ?` are literal and their backslashed forms are operators;
    `.`, `*`, `[...]`, `^` and `$` behave as usual.
    """
    out, i, n = [], 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == '\\' and i + 1 < n:
            nxt = pattern[i + 1]
            out.append(nxt if nxt in '(){}|+?' else '\\' + nxt)
            i += 2
        elif c == '[':
            j = i + 1
            body = ''
            if j < n and pattern[j] == '^':
                body, j = '^', j + 1
            if j < n and pattern[j] == ']':  # leading ] is literal
                body, j = body + r'\]', j + 1
            while j < n and pattern[j] != ']':
                m = re.match(r'\[:(\w+):\]', pattern[j:])
                if m:
                    body += POSIX_CLASSES.get(m.group(1), '')
                    j += m.end()
                else:
                    body += '\\' + pattern[j] if pattern[j] in '\\[' else pattern[j]
                    j += 1
            if j >= n:  # unterminated bracket: rest is literal
                out.append(re.escape(pattern[i:]))
                break
            out.append('[' + body + ']')
            i = j + 1
        elif c == '*' and (not out or out[-1] in ('(', '|', '^')):
            out.append(r'\*')
            i += 1
        elif c in '(){}|+?':
            out.append('\\' + c)
            i += 1
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def _bre_escape(text):
    return re.sub(r'([.\[\]*^$\\])', r'\\\1', text)


def _template_to_regex(template):
    """(display pattern, python regex, grep BRE) for a template, or None if too vague."""
    literal = ''.join(s for s in template if s is not WILDCARD)
    if len(literal.strip()) < MIN_LITERAL_CHARS:
        return None
    # Search is unanchored, so leading/trailing runtime values add nothing
    while template and template[0] is WILDCARD:
        template = template[1:]
    while template and template[-1] is WILDCARD:
        template = template[:-1]
    display, regex, bre = [], [], []
    for seg in template:
        if seg is WILDCARD:
            if display and display[-1] == '{…}':
                continue
            display.append('{…}')
            regex.append('.*?')
            bre.append('.*')
        else:
            display.append(seg)
            regex.append(bre_to_python(seg))
            bre.append(seg)
    regex, bre = ''.join(regex), ''.join(bre)
    try:
        re.compile(regex)
    except re.error:
        literal = ''.join(s for s in template if s is not WILDCARD)
        regex, bre = re.escape(literal), _bre_escape(literal)
    return ''.join(display), regex, bre


# ── expectation of a search_log call ────────────────────────────────────

def _is_success_check(node, var):
    """True for `var.success`, `var` (truthiness) or `var.returncode == 0`."""
    if isinstance(node, ast.Attribute) and node.attr == 'success':
        return isinstance(node.value, ast.Name) and node.value.id == var
    if isinstance(node, ast.Name):
        return node.id == var
    if isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute) and node.left.attr == 'returncode':
        return isinstance(node.left.value, ast.Name) and node.left.value.id == var
    return False


def _polarity(test, var):
    """+1 if `test` asserts var found, -1 if it asserts var not found, 0 otherwise."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _polarity(test.operand, var)
        return -inner
    if isinstance(test, ast.Compare) and isinstance(test.left, ast.Attribute) and test.left.attr == 'returncode':
        if isinstance(test.left.value, ast.Name) and test.left.value.id == var:
            op = test.ops[0]
            return 1 if isinstance(op, (ast.Eq, ast.Is)) else -1
    if isinstance(test, ast.BoolOp):
        for value in test.values:
            p = _polarity(value, var)
            if p:
                return p
    if _is_success_check(test, var):
        return 1
    return 0


def _body_fails(body):
    for node in body:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Raise):
                return True
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == 'fail'):
                return True
    return False


def _expectation(call, fn, parents):
    # Inline: assert device.search_log(...).success / assert not ...
    node = call
    while node in parents and not isinstance(node, ast.stmt):
        node = parents[node]
    stmt = node
    if isinstance(stmt, ast.Assert):
        test = stmt.test
        return 'absent' if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) else 'present'

    var = None
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
        var = stmt.targets[0].id
    if var is None:
        # results[p] = device.search_log(...) followed by asserts over results
        if isinstance(stmt, ast.Assign) and any(isinstance(n, ast.Assert) for n in ast.walk(fn)):
            return 'present'
        return 'info'

    for sub in ast.walk(fn):
        if getattr(sub, 'lineno', 0) <= stmt.lineno:
            continue
        if isinstance(sub, ast.Assert):
            p = _polarity(sub.test, var)
            if p:
                return 'present' if p > 0 else 'absent'
        elif isinstance(sub, ast.If) and _body_fails(sub.body):
            # `if not result.success: pytest.fail(...)` -> required
            p = _polarity(sub.test, var)
            if p:
                return 'present' if p < 0 else 'absent'
    return 'info'


# ── device type conditions ───────────────────────────────────────────────

# The framework's device types (conftest._derive_device_type_from_ota)
DEVICE_TYPES = frozenset({'krait', 'krait2', 'bagheera2', 'bagheera3', 'bagheera4', 'octo'})
DEVICE_TYPE_NAMES = {'device_type', 'dtype', 'dev_type'}


def _is_device_type(node):
    """device_type / device.device_type / self.device_type / device.variables["device_type"]."""
    if isinstance(node, ast.Name):
        return node.id in DEVICE_TYPE_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr == 'device_type'
    if isinstance(node, ast.Subscript):
        key = node.slice
        return isinstance(key, ast.Constant) and key.value == 'device_type'
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get':
        return bool(node.args) and isinstance(node.args[0], ast.Constant) and node.args[0].value == 'device_type'
    return False


def _strings(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [_strings(e) for e in node.elts]
        if all(v is not None for v in values):
            return set().union(*values) if values else set()
    return None


def device_type_set(cond):
    """Device types for which cond is true, or None when cond is not only about device_type."""
    if isinstance(cond, ast.BoolOp):
        parts = [device_type_set(v) for v in cond.values]
        if any(p is None for p in parts):
            return None
        out = set(parts[0])
        for p in parts[1:]:
            out = out & p if isinstance(cond.op, ast.And) else out | p
        return out
    if isinstance(cond, ast.UnaryOp) and isinstance(cond.op, ast.Not):
        inner = device_type_set(cond.operand)
        return None if inner is None else set(DEVICE_TYPES) - inner
    if isinstance(cond, ast.Compare) and len(cond.ops) == 1:
        left, op, right = cond.left, cond.ops[0], cond.comparators[0]
        if _is_device_type(left):
            values = _strings(right)
            if values is None:
                return None
            if isinstance(op, (ast.Eq, ast.In)):
                return set(DEVICE_TYPES) & values if isinstance(op, ast.In) or len(values) == 1 else None
            if isinstance(op, (ast.NotEq, ast.NotIn)):
                return set(DEVICE_TYPES) - values
        # "octo" in device_type: substring match
        if _is_device_type(right) and isinstance(op, (ast.In, ast.NotIn)):
            values = _strings(left)
            if values is None or len(values) != 1:
                return None
            sub_ = next(iter(values))
            hit = {t for t in DEVICE_TYPES if sub_ in t}
            return hit if isinstance(op, ast.In) else set(DEVICE_TYPES) - hit
    return None


def _stops(body):
    """The block skips the test or leaves the method (pytest.skip / return)."""
    if not body:
        return False
    last = body[-1]
    if isinstance(last, ast.Return):
        return True
    call = last.value if isinstance(last, ast.Expr) else None
    if isinstance(last, ast.Raise):
        call = last.exc
    return (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr in ('skip', 'xfail') )


def _early_exits(fn):
    """[(lineno, allowed device types)] for top-level `if <device_type cond>: return/skip`
    in a method: statements after lineno only run for the allowed types."""
    out = []
    for node in fn.body:
        if isinstance(node, ast.If) and not node.orelse and _stops(node.body):
            types = device_type_set(node.test)
            if types is not None:
                out.append((node.end_lineno, set(DEVICE_TYPES) - types))
    return out


def _call_device_types(call, parents, exits):
    """Device types a search call runs for: enclosing if/else branches and earlier exits."""
    allowed = set(DEVICE_TYPES)
    child, node = call, parents.get(call)
    while node is not None:
        if isinstance(node, ast.If):
            types = device_type_set(node.test)
            if types is not None:
                if child in node.body:
                    allowed &= types
                elif child in node.orelse:
                    allowed -= types
        child, node = node, parents.get(node)
    for lineno, types in exits:
        if call.lineno > lineno:
            allowed &= types
    return allowed


def _type_list(types):
    return None if types is None or set(types) >= DEVICE_TYPES else sorted(types)


def _call_arg(call, index, keyword):
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return call.args[index] if len(call.args) > index else None


def _extract_steps(fn, scopes):
    parents = {child: parent for parent in ast.walk(fn) for child in ast.iter_child_nodes(parent)}
    calls = sorted(
        (n for n in ast.walk(fn)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in SEARCH_METHODS),
        key=lambda n: (n.lineno, n.col_offset),
    )
    exits = _early_exits(fn)
    steps, unresolved = [], []
    for call in calls:
        multi = call.func.attr == 'search_logs_until_found'
        service_node = _call_arg(call, 0, 'service')
        pattern_node = _call_arg(call, 1, 'patterns' if multi else 'pattern')
        services = _resolve(service_node, scopes)
        templates = _resolve(pattern_node, scopes)
        source = ast.get_source_segment(fn._source, call) or ''
        base = {'method': fn.name, 'line': call.lineno, 'call': source[:300]}

        services = [s[0] for s in (services or []) if len(s) == 1 and s[0] is not WILDCARD]
        converted = [p for p in (_template_to_regex(t) for t in (templates or [])) if p]
        patterns = [display for display, _, _ in converted]
        regexes = [regex for _, regex, _ in converted]
        greps = [bre for _, _, bre in converted]
        if not services or not patterns:
            unresolved.append(dict(base, reason='service not resolvable' if not services
                                   else 'pattern only known at runtime'))
            continue
        # A list / loop variable means every pattern is searched; several plain
        # assignments (e.g. per device type) mean any one of them is expected.
        looped = isinstance(pattern_node, ast.Name) and any(
            isinstance(v, tuple) for v in scopes[0].get(pattern_node.id, []))
        mode = 'all' if multi or looped or isinstance(pattern_node, (ast.List, ast.Tuple)) else 'any'
        steps.append(dict(base, service=services[0], patterns=patterns, greps=greps, regexes=regexes, mode=mode,
                          expect=_expectation(call, fn, parents),
                          device_types=_type_list(_call_device_types(call, parents, exits))))
    return steps, unresolved


def extract_test_file(path, yaml_by_folder, yaml_by_number):
    source = path.read_text(errors='replace')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', SyntaxWarning)
            tree = ast.parse(source)
    except SyntaxError as e:
        return {'file': str(path), 'error': f'SyntaxError: {e}'}

    docstring = ast.get_docstring(tree) or ''
    folder = path.parent.name
    number = _tc_number(path.stem)
    m = TC_ID_RE.search(docstring)
    tc_id = m.group(1) if m else (f'TC_{number}' if number else path.stem)

    feature_text, feature_source = None, None
    m = FEATURE_RE.search(docstring)
    if m:
        feature_text, feature_source = m.group(1), 'docstring'
    elif number and (folder.lower(), number) in yaml_by_folder:
        feature_text, feature_source = yaml_by_folder[(folder.lower(), number)], 'yaml'
    elif number in yaml_by_number:
        feature_text, feature_source = yaml_by_number[number], 'yaml'
    feature, functionality = split_feature(feature_text) if feature_text else (None, '')

    module_scope = _scope_assigns(n for n in tree.body if not isinstance(n, (ast.ClassDef, ast.FunctionDef)))
    steps, unresolved = [], []
    test_types = set(DEVICE_TYPES)
    containers = [n for n in tree.body if isinstance(n, ast.ClassDef)] or [tree]
    for container in containers:
        class_scope = _scope_assigns(container.body) if isinstance(container, ast.ClassDef) else {}
        methods = sorted(
            (n for n in container.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name.startswith('test')),
            key=lambda n: n.lineno,
        )
        for fn in methods:
            fn._source = source
            # A precondition that skips/returns for some device types: the test is not for them
            if 'precondition' in fn.name:
                for _, types in _early_exits(fn):
                    test_types &= types
            scopes = [_scope_assigns(fn.body), class_scope, module_scope]
            s, u = _extract_steps(fn, scopes)
            steps.extend(s)
            unresolved.extend(u)

    return {
        'file': str(path),
        'folder': folder,
        'name': path.stem,
        'tc_id': tc_id,
        'feature': feature,
        'feature_source': feature_source,
        'functionality': functionality or path.stem,
        'device_types': _type_list(test_types),
        'steps': steps,
        'unresolved': unresolved,
    }


def extract_all(tests_dir=DEFAULT_TESTS_DIR):
    tests_dir = Path(tests_dir)
    yaml_by_folder, yaml_by_number = _load_yaml_features(tests_dir.parent / 'test_cases')
    tests = [extract_test_file(p, yaml_by_folder, yaml_by_number)
             for p in sorted(tests_dir.rglob('test_*.py'))]

    # Undocumented tests take the most common feature of their folder
    folder_features = defaultdict(Counter)
    for t in tests:
        if t.get('feature'):
            folder_features[t['folder']][t['feature']] += 1
    for t in tests:
        if 'error' in t or t['feature']:
            continue
        common = folder_features[t['folder']].most_common(1)
        t['feature'] = common[0][0] if common else t['folder']
        t['feature_source'] = 'folder'
    return tests


def extract_all_cached(tests_dir=DEFAULT_TESTS_DIR, snapshot=None):
    """extract_all(), refreshing a JSON snapshot; the snapshot is used when tests_dir
    isn't available (e.g. inside the Docker image, which has no device-automation
    checkout)."""
    snapshot = Path(snapshot) if snapshot else None
    if Path(tests_dir).is_dir():
        tests = extract_all(tests_dir)
        if snapshot:
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            tmp = snapshot.with_suffix('.tmp')
            tmp.write_text(json.dumps({'tests_dir': str(tests_dir), 'tests': tests}, default=str))
            tmp.replace(snapshot)
        return tests
    if snapshot and snapshot.exists():
        log.warning('%s not found, using test steps snapshot %s (modified %s)', tests_dir, snapshot,
                    datetime.fromtimestamp(snapshot.stat().st_mtime).isoformat(timespec='seconds'))
        return json.loads(snapshot.read_text())['tests']
    raise FileNotFoundError(f'Tests directory {tests_dir} not found and no snapshot at {snapshot}')


if __name__ == '__main__':
    import sys
    tests = extract_all(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TESTS_DIR)
    ok = [t for t in tests if 'error' not in t]
    print(json.dumps({
        'files': len(tests),
        'parse_errors': len(tests) - len(ok),
        'with_steps': sum(1 for t in ok if t['steps']),
        'steps': sum(len(t['steps']) for t in ok),
        'unresolved': sum(len(t['unresolved']) for t in ok),
        'expect': Counter(s['expect'] for t in ok for s in t['steps']),
        'tests_limited_to_device_types': sum(1 for t in ok if t.get('device_types')),
        'steps_limited_to_device_types': sum(1 for t in ok for s in t['steps'] if s.get('device_types')),
        'feature_source': Counter(t['feature_source'] for t in ok),
        'features': len({t['feature'] for t in ok}),
    }, indent=2, default=str))
