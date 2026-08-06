"""result_store.py — short-lived in-memory store for downloadable query results."""

from __future__ import annotations

import csv
import io
import threading
import time
import uuid

_DEFAULT_TTL = 3600  # 1 hour


class ResultStore:
    def __init__(self, ttl: float = _DEFAULT_TTL) -> None:
        self._store: dict[str, tuple[bytes, str, float]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl

    def put(self, data: bytes, filename: str) -> str:
        result_id = uuid.uuid4().hex
        with self._lock:
            self._purge_locked()
            self._store[result_id] = (data, filename, time.time())
        return result_id

    def get(self, result_id: str) -> tuple[bytes, str] | None:
        with self._lock:
            entry = self._store.get(result_id)
            if entry is None:
                return None
            data, filename, ts = entry
            if time.time() - ts > self._ttl:
                del self._store[result_id]
                return None
            return data, filename

    def _purge_locked(self) -> None:
        cutoff = time.time() - self._ttl
        stale = [k for k, (_, _, ts) in self._store.items() if ts < cutoff]
        for k in stale:
            del self._store[k]


def rows_to_csv_bytes(columns: list[str], rows: list) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([str(v) if v is not None else "" for v in row])
    return buf.getvalue().encode("utf-8")


result_store = ResultStore()
