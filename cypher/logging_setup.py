"""Rotating file logger, matching the atlas sub-agents' logging convention."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import LOG_DIR

_NAME = "cypher.kg_agent"


def get_logger() -> logging.Logger:
    logger = logging.getLogger(_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    try:
        LOG_DIR.mkdir(exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            LOG_DIR / "cypher_kg_agent.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:
        handler = logging.StreamHandler()

    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger
