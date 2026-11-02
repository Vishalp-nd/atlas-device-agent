"""serve_ui.py — host the Log Analyzer UI on its own, pointed at a backend set in .env.

For a UI on a different server from the Atlas backend (e.g. reaching it through an
SSH tunnel). Reads ATLAS_API_URL from atlas-device-agent/.env (or the environment)
and hands only that value to the browser as config.js; nothing else in .env is exposed.

    # .env on the UI server
    ATLAS_API_URL=http://<this-server>:8502     # or just the port: ATLAS_API_URL=8502

    python -m log_analyzer.serve_ui --port 8080          # serve log_analyzer/ui/
    python -m log_analyzer.serve_ui --write              # or write ui/config.js for nginx etc.

Run from atlas-device-agent/. The browser must be able to reach ATLAS_API_URL, so use
the server's hostname rather than localhost unless the browser runs on that machine.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from dotenv import dotenv_values

from .config import REPO_ROOT, UI_DIR

ENV_FILE = REPO_ROOT / ".env"


def api_url() -> str:
    """ATLAS_API_URL from the environment, else from atlas-device-agent/.env."""
    value = os.environ.get("ATLAS_API_URL") or dotenv_values(ENV_FILE).get("ATLAS_API_URL") or ""
    return value.strip()


def config_js() -> bytes:
    return f"window.ATLAS_API_URL = {json.dumps(api_url())};\n".encode()


class _Handler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/config.js":
            body = config_js()  # read per request, so .env edits apply on reload
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--write", action="store_true", help="write ui/config.js and exit instead of serving")
    args = parser.parse_args()

    if not api_url():
        print(f"warning: ATLAS_API_URL is not set in the environment or {ENV_FILE}; "
              "the UI will fall back to port 8501 on its own host")
    if args.write:
        (UI_DIR / "config.js").write_bytes(config_js())
        print(f"wrote {UI_DIR / 'config.js'} (ATLAS_API_URL={api_url()!r})")
        return

    handler = functools.partial(_Handler, directory=str(UI_DIR))
    print(f"Log Analyzer UI on http://{args.host}:{args.port}/  ->  API {api_url() or '(default :8501)'}")
    ThreadingHTTPServer((args.host, args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
