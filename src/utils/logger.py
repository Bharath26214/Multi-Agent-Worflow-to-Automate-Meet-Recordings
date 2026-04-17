from __future__ import annotations

import logging
import os


def get_logger(name: str) -> logging.Logger:
    """Return a configured project logger."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level_name = os.getenv("APP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    handler.setFormatter(formatter)

    logger.setLevel(level)
    logger.addHandler(handler)
    logger.propagate = False
    return logger

