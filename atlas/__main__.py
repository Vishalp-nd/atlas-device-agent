"""
__main__.py — CLI entry point for the Atlas Device Agent API.

Usage:
    # Run the Atlas FastAPI service
    python -m atlas serve
    python -m atlas serve --port 9000 --reload

(run from the repo root, atlas-device-agent/, so `atlas` resolves as a
top-level package)
"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="atlas",
        description="Atlas Device Agent — FastAPI service hosting the supervisor and sub-agents.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve_parser = sub.add_parser("serve", help="Run the Atlas FastAPI service")
    serve_parser.add_argument(
        "--host", default="0.0.0.0",
        help="Bind host (default: 0.0.0.0)",
    )
    serve_parser.add_argument(
        "--port", type=int, default=8000,
        help="Bind port (default: 8000)",
    )
    serve_parser.add_argument(
        "--reload", action="store_true",
        help="Auto-reload on code changes (development only)",
    )

    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn
        uvicorn.run(
            "atlas.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )


if __name__ == "__main__":
    main()
