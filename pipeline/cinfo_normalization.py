#!/usr/bin/env python3
"""Single source of truth for critical-info description normalization.

Any whitespace-delimited token containing a digit is treated as variable data and replaced with
<N>, then the result is trimmed. cinfo_classifier.py (Python-side matching),
reupdate_clickhouse_types.py and scripts/sync_clickhouse_cinfo_priority_map.py (ClickHouse-side
SQL expressions) all build from these same constants so the SQL-side and Python-side normalization
can never drift apart. Kept dependency-free (stdlib only) so every other module in this chain can
import from it without risking a circular import.

The character classes below are spelled out explicitly (e.g. [0-9] instead of \\d, [\\t\\n\\f\\r ]
instead of \\s) rather than using Python's regex shorthands. ClickHouse's replaceRegexpAll runs on
RE2, whose \\d/\\s/\\S are ASCII-only ([0-9] and [\\t\\n\\f\\r ], notably without \\v) -- but Python's
`re` module treats \\d/\\s/\\S as Unicode-aware by default (e.g. \\d also matches non-ASCII decimal
digits). Spelling the classes out avoids relying on those two engines' shorthand defaults agreeing,
so this is provably byte-for-byte identical to the SQL side rather than "identical for ASCII input".
"""

from __future__ import annotations

import re

NORMALIZE_TOKEN_REGEX = r"[^\t\n\f\r ]*[0-9][^\t\n\f\r ]*"
NORMALIZE_TOKEN_REPL = "<N>"
_STRIP_CHARS = "\t\n\f\r "


def normalize_description(description: str | None) -> str:
    substituted = re.sub(NORMALIZE_TOKEN_REGEX, NORMALIZE_TOKEN_REPL, description or "")
    return substituted.strip(_STRIP_CHARS)
