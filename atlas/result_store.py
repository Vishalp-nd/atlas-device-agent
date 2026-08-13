"""result_store.py — short-lived store for downloadable query results."""

from __future__ import annotations

import csv
import io
from pathlib import Path
import threading
import time
import uuid

_DEFAULT_TTL = 3600  # 1 hour


class ResultStore:
    def __init__(self, ttl: float = _DEFAULT_TTL) -> None:
        self._store: dict[str, tuple[str, str | Path, str, float]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl

    def put(self, data: bytes, filename: str) -> str:
        result_id = uuid.uuid4().hex
        with self._lock:
            self._purge_locked()
            self._store[result_id] = ("bytes", data, filename, time.time())
        return result_id

    def put_file(self, file_path: str | Path, filename: str | None = None) -> str:
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Download file not found: {path}")

        result_id = uuid.uuid4().hex
        with self._lock:
            self._purge_locked()
            self._store[result_id] = ("file", path, filename or path.name, time.time())
        return result_id

    def get(self, result_id: str) -> tuple[bytes, str] | None:
        with self._lock:
            entry = self._store.get(result_id)
            if entry is None:
                return None
            kind, payload, filename, ts = entry
            if time.time() - ts > self._ttl:
                del self._store[result_id]
                return None
            if kind == "bytes":
                return payload, filename

            path = Path(payload)
            if not path.is_file():
                del self._store[result_id]
                return None
            return path.read_bytes(), filename

    def _purge_locked(self) -> None:
        cutoff = time.time() - self._ttl
        stale = [k for k, (_, _, _, ts) in self._store.items() if ts < cutoff]
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
