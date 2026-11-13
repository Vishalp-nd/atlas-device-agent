"""serve_ui.py — host the Log Analyzer UI on its own, pointed at a backend set in .env.

For a UI on a different server from the Atlas backend (e.g. reaching it through an
SSH tunnel). Reads ATLAS_API_URL from atlas-device-agent/.env (or the environment)
and hands only that value to the browser as config.js; nothing else in .env is exposed.

    # .env on the UI server
    ATLAS_API_URL=http://172.16.18.142:8502     # full URL: http://<host>:<port>

    python -m log_analyzer.serve_ui --port 8080          # serve log_analyzer/ui/
    python -m log_analyzer.serve_ui --write              # or write ui/config.js for nginx etc.

Run from atlas-device-agent/. The browser must be able to reach ATLAS_API_URL, so use
the server's hostname rather than localhost unless the browser runs on that machine.

OTA runs when the backend can't reach the device db: for the environments in
UI_RESOLVES_OTA_DEVICES (.env or environment, comma separated; default "stag", empty = none)
the UI gets the OTA's devices from this server, GET /ota-devices?environment=&ota=&max_devices=
(log_analyzer/devices.py: same query, same 200 cap as the backend, using this server's
db_credentials.ini), and sends them with the run like a device-ID run.
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import os
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from dotenv import dotenv_values

from .config import ENVIRONMENTS, REPO_ROOT, UI_DIR

ENV_FILE = REPO_ROOT / ".env"
log = logging.getLogger("log_analyzer.serve_ui")
OTA_RE = re.compile(r"^[\w.\-]+$")  # as log_analyzer/service.py (not imported: it needs the run db)


def api_url() -> str:
    """ATLAS_API_URL from the environment, else from atlas-device-agent/.env."""
    value = os.environ.get("ATLAS_API_URL") or dotenv_values(ENV_FILE).get("ATLAS_API_URL") or ""
    return value.strip()


def _setting(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        value = dotenv_values(ENV_FILE).get(name)
    return (default if value is None else value).strip()


def device_list_envs() -> list[str]:
    """Environments whose OTA device list this server resolves (UI_RESOLVES_OTA_DEVICES, default stag)."""
    return [e for e in (p.strip() for p in _setting("UI_RESOLVES_OTA_DEVICES", "stag").split(",")) if e in ENVIRONMENTS]


def config_js(static: bool = False) -> bytes:
    """static: config.js for a plain web server (--write), which has no /ota-devices."""
    return (f"window.ATLAS_API_URL = {json.dumps(api_url())};\n"
            f"window.UI_RESOLVES_OTA_DEVICES = {json.dumps([] if static else device_list_envs())};\n").encode()


def ota_devices_response(query: str) -> tuple[int, dict]:
    """GET /ota-devices: (status, body) with the OTA's devices, as the backend worker would pick them."""
    params = {k: v[-1].strip() for k, v in parse_qs(query).items()}
    environment, ota = params.get("environment", ""), params.get("ota", "")
    if environment not in device_list_envs():
        return 400, {"detail": f"This UI server doesn't resolve devices for environment {environment!r}"}
    if not OTA_RE.match(ota):
        return 400, {"detail": "Enter a valid OTA version"}
    try:
        max_devices = int(params["max_devices"]) if params.get("max_devices") else None
    except ValueError:
        return 400, {"detail": "max_devices must be a number"}
    try:
        from .devices import ota_devices
        found = ota_devices(environment, ota, max_devices)
    except Exception as exc:  # db unreachable, bad credentials, psycopg2 missing, ...
        log.exception("ota-devices %s %s failed", environment, ota)
        return 502, {"detail": f"Could not get the devices on OTA {ota} from "
                               f"{ENVIRONMENTS[environment]['device_db']}: {type(exc).__name__}: {exc}"}
    log.info("ota-devices %s %s: %d of %d device(s)", environment, ota, len(found["devices"]), found["total"])
    return 200, {"environment": environment, "ota": ota, **found}


class _Handler(SimpleHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path == "/config.js":
            # read per request, so .env edits apply on reload
            self._send(200, config_js(), "application/javascript; charset=utf-8")
            return
        if url.path == "/ota-devices":
            status, body = ota_devices_response(url.query)
            self._send(status, json.dumps(body).encode(), "application/json")
            return
        super().do_GET()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--write", action="store_true", help="write ui/config.js and exit instead of serving")
    args = parser.parse_args()

    url = api_url()
    if not url:
        print(f"warning: ATLAS_API_URL is not set in the environment or {ENV_FILE}; "
              "the UI will fall back to port 8501 on its own host")
    elif not url.startswith(("http://", "https://")):
        parser.error(f"ATLAS_API_URL must be a full URL like http://172.16.18.142:8502, got {url!r}")
    if args.write:
        (UI_DIR / "config.js").write_bytes(config_js(static=True))
        print(f"wrote {UI_DIR / 'config.js'} (ATLAS_API_URL={api_url()!r})")
        return

    # An unreachable device db fails the request in seconds instead of hanging it (libpq)
    os.environ.setdefault("PGCONNECT_TIMEOUT", "15")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler = functools.partial(_Handler, directory=str(UI_DIR))
    print(f"Log Analyzer UI on http://{args.host}:{args.port}/  ->  API {api_url() or '(default :8501)'}")
    envs = device_list_envs()
    print("OTA device lists resolved here for: " + (", ".join(
        f"{e} ({ENVIRONMENTS[e]['device_db']})" for e in envs) if envs else "none (the backend resolves them)"))
    ThreadingHTTPServer((args.host, args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
