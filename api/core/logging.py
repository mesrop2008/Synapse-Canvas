"""Uvicorn configures only its own loggers; without this, app records reach a
handler-less root and vanish."""

from __future__ import annotations

import logging

_CONFIGURED = False


def configure_logging(level: int | str = logging.INFO) -> None:
    # create_app() runs per test; a handler each time multiplies every line.
    global _CONFIGURED
    if _CONFIGURED:
        return

    app_logger = logging.getLogger("api")
    app_logger.setLevel(level)
    app_logger.propagate = False

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    app_logger.addHandler(handler)
    _CONFIGURED = True
