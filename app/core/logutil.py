import json
import logging
import sys
from typing import Any


# Turns log records into one JSON line per event (for Railway / Logfire).
class JsonFormatter(logging.Formatter):
    # Build the JSON payload from a single log record.
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
            "logger": record.name,
        }
        # Extra fields passed via logger.info("...", extra={...})
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "event", "taskName",
            }:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


# Configure stdout logging with JSON formatting for the whole process.
def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # HTTPX/requests instrumentation and the visible-outbound span retain the
    # useful request timing/status. Keep only warnings/errors from library
    # loggers so headers and duplicate summaries do not obscure the agent trace.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("twilio.http_client").setLevel(logging.WARNING)


# Get a named logger (use __name__ in each module).
def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
