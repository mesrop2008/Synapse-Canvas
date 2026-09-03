"""Application logging setup.

Uvicorn configures only its own loggers (`uvicorn`, `uvicorn.access`). The
application's loggers propagate to the root logger, which by default has no
handler, so every `logger.info`/`logger.exception` the app emits is silently
discarded -- including the dev-only email link and, worse, the tracebacks the
catch-all error handler records. This attaches a handler to the application's
own logger namespace so those records actually surface.

Only the `app` namespace is touched. Uvicorn keeps ownership of request/access
logging, so there is no double-configuration and no duplicate lines.
"""

from __future__ import annotations

import logging

_CONFIGURED = False


def configure_logging(level: int | str = logging.INFO) -> None:
    """Attach a stream handler to the `app` logger, once per process.

    Idempotent: `create_app()` runs many times in the test suite, and a fresh
    handler on each call would multiply every log line.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    app_logger = logging.getLogger("app")
    app_logger.setLevel(level)
    # Do not bubble up to the root logger as well; this handler is the one and
    # only sink for app records.
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
