import logging

from app.core.logutil import setup_logging


def test_setup_logging_keeps_http_library_info_out_of_agent_traces() -> None:
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_root_level = root.level
    noisy_loggers = [logging.getLogger("httpx"), logging.getLogger("twilio.http_client")]
    old_levels = [logger.level for logger in noisy_loggers]

    try:
        setup_logging()
        assert all(logger.level == logging.WARNING for logger in noisy_loggers)
    finally:
        root.handlers[:] = old_handlers
        root.setLevel(old_root_level)
        for logger, level in zip(noisy_loggers, old_levels, strict=True):
            logger.setLevel(level)
