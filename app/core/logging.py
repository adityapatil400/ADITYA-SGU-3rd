import logging
import re
import sys
from typing import Any
from app.core.config import settings

# Patterns for sensitive keys that should be masked in logs
SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|token|secret|authorization|api_key|dsn|connection_string)",
    re.IGNORECASE
)
URL_CREDENTIAL_PATTERN = re.compile(r"://([^:]+):([^@]+)@")


class SensitiveDataFilter(logging.Filter):
    """Filter that masks passwords and sensitive connection parameters in log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            # Mask URL credentials (e.g., postgresql://user:pass@host)
            record.msg = URL_CREDENTIAL_PATTERN.sub(r"://\1:***@", record.msg)
        return True


def setup_logging() -> None:
    """Configures centralized structured logging for FlowGrid."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    
    # Root handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    handler.addFilter(SensitiveDataFilter())

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] [%(process)d]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)

    # Configure application logger
    app_logger = logging.getLogger("flowgrid")
    app_logger.setLevel(log_level)
    app_logger.handlers.clear()
    app_logger.addHandler(handler)
    app_logger.propagate = False

    # Adjust external loggers to reduce noise
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


logger = logging.getLogger("flowgrid")
