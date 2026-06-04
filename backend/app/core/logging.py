"""Structured logging configuration."""

import logging
import sys
from typing import Optional


def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging for the application."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a named logger."""
    return logging.getLogger(name or "9xaipal")

