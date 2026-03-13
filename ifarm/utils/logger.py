"""Structured JSON logger with optional per-device context.

Every log line is a single JSON object, making it easy to parse with
jq, ship to a log aggregator, or correlate by UDID across a swarm.

Usage:
    from ifarm.utils.logger import get_logger

    log = get_logger(__name__, device_udid="abc123")
    log.info("rotating IP", extra={"attempt": 1})
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class _JSONFormatter(logging.Formatter):
    def __init__(self, device_udid: str | None = None):
        super().__init__()
        self._udid = device_udid

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if self._udid:
            payload["udid"] = self._udid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # Attach any extra= fields passed by the caller
        _std_keys = logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
        ctx = {k: v for k, v in record.__dict__.items() if k not in _std_keys}
        if ctx:
            payload["ctx"] = ctx

        return json.dumps(payload)


def get_logger(name: str, device_udid: str | None = None) -> logging.Logger:
    """Return a JSON-structured logger, optionally tagged with a device UDID.

    Calling get_logger with the same name twice returns the same logger
    instance (standard Python logging behavior). The formatter is only
    attached on first call.

    Args:
        name: Logger name — typically pass __name__ from the calling module.
        device_udid: If provided, every log line includes this UDID for
            cross-device correlation in swarm scenarios.

    Returns:
        Configured Logger instance.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JSONFormatter(device_udid=device_udid))
        logger.addHandler(handler)
        logger.propagate = False
        logger.setLevel(logging.INFO)
    return logger
