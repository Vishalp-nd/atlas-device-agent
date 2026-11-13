"""devices.py — devices currently on an OTA (nddevicemaster.c_p_version), for OTA runs.

Used by the backend worker (log_analyzer/worker.py) and by the UI server
(log_analyzer/serve_ui.py, GET /ota-devices): a backend that can't reach an environment's
device db gets the list from the UI, which sends it with the run (RunCreateRequest.devices).
Only needs psycopg2 and db_credentials.ini, so the UI server doesn't need the rest of Atlas.
"""

from __future__ import annotations

from .config import ENVIRONMENTS, OTA_DEVICE_LIMIT

STATE_MAP = {
    -1: "SHIPPED_FROM_VENDOR", 1: "NEW", 2: "SHIPPED_ACTIVE", 3: "SHIPPED_SPARE", 4: "INSTALLED",
    5: "RMA_INITIATED", 6: "RETURN_INITIATED_NON_RMA", 7: "RETURNED_RMA", 8: "RETURNED_NON_RMA",
    9: "DEAD", 10: "RMA_PENDING", 11: "NON_RMA_PENDING", 12: "NF_OOW",
}
# Devices that are out in the field log the most; list them first when capping
_STATE_ORDER = {"INSTALLED": 0, "SHIPPED_ACTIVE": 1}


def device_limit(max_devices: int | None = None) -> int:
    """The run's cap: max_devices if given, never above OTA_DEVICE_LIMIT (200)."""
    return min(int(max_devices or OTA_DEVICE_LIMIT), OTA_DEVICE_LIMIT)


def ota_devices(environment: str, ota: str, max_devices: int | None = None) -> dict:
    """{devices (capped, field devices first), total, states {state: count}, limit, device_db}."""
    from db_login.db_login import db_connect

    device_db = ENVIRONMENTS[environment]["device_db"]
    conn = db_connect(device_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT manufacturer_device_id, state_id FROM nddevicemaster WHERE c_p_version = %s",
                        (ota.strip(),))
            rows = [(str(d), STATE_MAP.get(int(s), str(s))) for d, s in cur.fetchall()]
    finally:
        conn.close()
    rows.sort(key=lambda r: _STATE_ORDER.get(r[1], 2))  # stable: db order within a state
    devices = list(dict.fromkeys(d for d, _ in rows))
    states: dict[str, int] = {}
    for _, state in rows:
        states[state] = states.get(state, 0) + 1
    limit = device_limit(max_devices)
    return {"devices": devices[:limit], "total": len(devices), "limit": limit, "device_db": device_db,
            "states": dict(sorted(states.items(), key=lambda kv: -kv[1]))}
