from datetime import datetime
from typing import List, Dict, Tuple


def _to_dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _to_str(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _merge_intervals(intervals: List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
    if not intervals:
        return []

    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    merged = [sorted_intervals[0]]

    for start, end in sorted_intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    return merged


def _ensure_registry_table(table: str, conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS public.{table} (
                device_id TEXT PRIMARY KEY,
                date_range JSONB NOT NULL DEFAULT '[]'::jsonb,
                ota TEXT
            )
            """
        )
    conn.commit()


def subtract_date_range_main(
    table: str,
    device_id: str,
    loc_conn,
    start_date: str,
    end_date: str,
) -> List[Dict[str, str]]:
    _ensure_registry_table(table, loc_conn)

    requested_start = _to_dt(start_date)
    requested_end = _to_dt(end_date)

    with loc_conn.cursor() as cur:
        cur.execute(
            f"SELECT date_range FROM public.{table} WHERE device_id = %s",
            (str(device_id),),
        )
        row = cur.fetchone()

    processed = row[0] if row and row[0] else []

    pending: List[Tuple[datetime, datetime]] = [(requested_start, requested_end)]

    for item in processed:
        try:
            proc_start = _to_dt(item["sd"])
            proc_end = _to_dt(item["ed"])
        except Exception:
            continue

        next_pending: List[Tuple[datetime, datetime]] = []
        for cur_start, cur_end in pending:
            if proc_end <= cur_start or proc_start >= cur_end:
                next_pending.append((cur_start, cur_end))
                continue

            if proc_start > cur_start:
                next_pending.append((cur_start, proc_start))
            if proc_end < cur_end:
                next_pending.append((proc_end, cur_end))

        pending = next_pending

    return [{"sd": _to_str(s), "ed": _to_str(e)} for s, e in pending if s < e]


def update_registry_data(
    table: str,
    device_id: str,
    loc_conn,
    sd: str,
    ed: str,
    ota: str | None = None,
) -> None:
    _ensure_registry_table(table, loc_conn)

    with loc_conn.cursor() as cur:
        cur.execute(
            f"SELECT date_range FROM public.{table} WHERE device_id = %s",
            (str(device_id),),
        )
        row = cur.fetchone()

        existing = row[0] if row and row[0] else []
        intervals: List[Tuple[datetime, datetime]] = []

        for item in existing:
            try:
                intervals.append((_to_dt(item["sd"]), _to_dt(item["ed"])))
            except Exception:
                continue

        intervals.append((_to_dt(sd), _to_dt(ed)))
        merged = _merge_intervals(intervals)
        payload = [{"sd": _to_str(s), "ed": _to_str(e)} for s, e in merged]

        cur.execute(
            f"""
            INSERT INTO public.{table} (device_id, date_range, ota)
            VALUES (%s, %s::jsonb, %s)
            ON CONFLICT (device_id)
            DO UPDATE SET
                date_range = EXCLUDED.date_range,
                ota = COALESCE(EXCLUDED.ota, public.{table}.ota)
            """,
            (str(device_id), __import__("json").dumps(payload), ota),
        )

    loc_conn.commit()
