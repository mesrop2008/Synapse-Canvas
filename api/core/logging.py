"""Attach a handler to the `app` logger namespace.

Uvicorn configures only its own loggers, so the app's records would otherwise
propagate to a handler-less root and vanish -- including the dev email link and
the catch-all's tracebacks. Only `app` is touched, so there are no duplicates.
"""

from __future__ import annotations

import logging

_CONFIGURED = False


def configure_logging(level: int | str = logging.INFO) -> None:
    # Idempotent: create_app() runs many times in the suite, and a fresh
    # handler each time would multiply every line.
    global _CONFIGURED
    if _CONFIGURED:
        return

    app_logger = logging.getLogger("app")
    app_logger.setLevel(level)
    app_logger.propagate = False  # this handler is the only sink for app records

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    app_logger.addHandler(handler)
    _CONFIGURED = True
