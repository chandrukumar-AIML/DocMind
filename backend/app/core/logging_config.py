# backend/app/core/logging_config.py
# DVMELTSS-FIX: M - Modular, L - Logging, S - Security
# ASCALE-FIX: S - Separation, C - Coupling
"""
Structured logging configuration for DocuMind AI.

Features:
- Text or JSON output format
- Optional rotating file handler for production
- Suppression of noisy third-party loggers
- Safe level lookup with fallback

Usage:
    from app.core.logging_config import configure_logging, get_request_logger
    configure_logging(level="INFO", log_format="json", log_file="logs/app.log")
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

# ✅ FIXED: Pylance-friendly import with fallback
try:
    from pythonjsonlogger import jsonlogger  # type: ignore[import-untyped]
except ImportError:
    # Fallback: install with `pip install python-json-logger`
    jsonlogger = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


class CustomJsonFormatter(jsonlogger.JsonFormatter if jsonlogger else logging.Formatter):  # type: ignore[misc]
    """
    Custom JSON formatter with additional fields for observability.

    Falls back to standard Formatter if python-json-logger is unavailable.
    """

    def add_fields(self, log_record, record, message_dict):
        if jsonlogger:
            super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = record.created
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        # Add request_id if present in record (for distributed tracing)
        if hasattr(record, "request_id"):
            log_record["request_id"] = record.request_id


def configure_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    log_format: str = "text",  # "text" or "json"
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 3,
) -> None:
    """
    Configure structured logging for all app modules.

    Features:
    - Text or JSON output format
    - Optional rotating file handler for production
    - Suppression of noisy third-party loggers
    - Safe level lookup with fallback
    """
    # Clear existing handlers to avoid duplicates on reload
    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    # Set root level
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root_logger.setLevel(numeric_level)

    # Create formatter based on format choice
    if log_format == "json" and jsonlogger:
        formatter = CustomJsonFormatter(
            "%(timestamp)s %(level)s %(name)s %(message)s",
            rename_fields={"message": "msg"},
        )
    else:
        if log_format == "json" and not jsonlogger:
            logger.warning("python-json-logger not available — falling back to text format")
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )

    # Always add stdout handler (required for container logging)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(numeric_level)
    root_logger.addHandler(console_handler)

    # Add rotating file handler if log_file specified
    if log_file:
        try:
            from logging.handlers import RotatingFileHandler

            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            file_handler = RotatingFileHandler(
                str(log_path),
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(numeric_level)
            root_logger.addHandler(file_handler)

            logging.info(f"Logging to file: {log_path} (max {max_bytes/1024/1024:.1f}MB, {backup_count} backups)")

        except ImportError:
            logging.warning("RotatingFileHandler not available — file logging disabled")
        except Exception as e:
            logging.warning(f"Failed to configure file logging: {e}")

    # Suppress noisy third-party loggers
    noisy_loggers = {
        "paddleocr": logging.WARNING,
        "ppstructure": logging.WARNING,
        "openai": logging.WARNING,
        "httpx": logging.WARNING,
        "httpcore": logging.WARNING,
        "chromadb": logging.WARNING,
        "chromadb.config": logging.ERROR,
        "chromadb.telemetry": logging.CRITICAL,
        "chromadb.telemetry.product.posthog": logging.CRITICAL,
        "mlflow": logging.WARNING,
        "urllib3": logging.WARNING,
        "botocore": logging.WARNING,
        "boto3": logging.WARNING,
    }

    for logger_name, log_level in noisy_loggers.items():
        logging.getLogger(logger_name).setLevel(log_level)

    logging.info(f"Logging configured: level={level}, format={log_format}")


def get_request_logger(request_id: Optional[str] = None) -> logging.Logger:
    """
    Get a logger with optional request_id for distributed tracing.

    Usage:
        logger = get_request_logger(request_id="abc123")
        logger.info("Processing request", extra={"request_id": request_id})

    Args:
        request_id: Optional unique identifier for request tracing

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger("app.request")
    if request_id:
        # Use adapter to inject request_id into all log records
        return logging.LoggerAdapter(logger, {"request_id": request_id})
    return logger


# DVMELTSS-M: Explicit module exports
__all__ = ["configure_logging", "get_request_logger", "CustomJsonFormatter"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
