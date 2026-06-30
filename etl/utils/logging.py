"""Centralized logging configuration for the ETL orchestrator.

This module provides a standardized logger factory, structured logging support,
and safe exception handling without exposing secrets.
"""

from __future__ import annotations

import json
import logging
import os
import re
from contextlib import contextmanager
from typing import Any, Generator, Optional

# Sensitive field patterns to redact
SENSITIVE_PATTERNS = [
    r"password",
    r"secret",
    r"token",
    r"api[_-]?key",
    r"auth",
]

# Compile regex for matching sensitive fields
SENSITIVE_REGEX = re.compile(
    "|".join(SENSITIVE_PATTERNS),
    re.IGNORECASE,
)


def redact_sensitive_values(data: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive values from a dictionary.

    Args:
        data: Dictionary potentially containing sensitive fields.

    Returns:
        Dictionary with sensitive values replaced with [REDACTED].
    """
    redacted = {}

    for key, value in data.items():
        if SENSITIVE_REGEX.search(key):
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value

    return redacted


class StructuredFormatter(logging.Formatter):
    """Formatter that outputs structured JSON logs with context fields.

    The formatted log includes:
    - timestamp
    - level
    - module
    - message
    - context fields (batch_id, source_type, record_id, status, etc.)
    """

    def __init__(self, include_json: bool = True):
        """Initialize the formatter.

        Args:
            include_json: If True, format as JSON. Otherwise use key-value format.
        """
        super().__init__()
        self.include_json = include_json

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as structured JSON or key-value output."""
        log_data = {
            "timestamp": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }

        # Standard LogRecord attributes to skip
        standard_attrs = {
            "name",
            "msg",
            "args",
            "created",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "message",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "thread",
            "threadName",
            "exc_info",
            "exc_text",
            "stack_info",
            "asctime",
        }

        # Add custom context fields (non-standard attributes)
        for key, value in record.__dict__.items():
            if key not in standard_attrs and value is not None:
                log_data[key] = value

        # Add exception details if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Redact sensitive values
        log_data = redact_sensitive_values(log_data)

        if self.include_json:
            return json.dumps(log_data)

        # Key-value format fallback
        pairs = [f"{k}={v}" for k, v in log_data.items()]
        return " ".join(pairs)


class ContextualLogger:
    """Logger wrapper that supports context enrichment.

    Allows setting context fields that are automatically included in all
    subsequent log messages until the context is cleared.
    """

    def __init__(self, logger: logging.Logger):
        """Initialize the contextual logger.

        Args:
            logger: The underlying Python logger instance.
        """
        self.logger = logger
        self.context: dict[str, Any] = {}

    def set_context(
        self,
        batch_id: Optional[str] = None,
        source_type: Optional[str] = None,
        record_id: Optional[str] = None,
        status: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Set context fields for subsequent log messages.

        Args:
            batch_id: Batch or run identifier.
            source_type: Data source type (csv, api, mongo).
            record_id: Unique record identifier.
            status: Current processing status.
            **kwargs: Additional context fields.
        """
        if batch_id is not None:
            self.context["batch_id"] = batch_id

        if source_type is not None:
            self.context["source_type"] = source_type

        if record_id is not None:
            self.context["record_id"] = record_id

        if status is not None:
            self.context["status"] = status

        self.context.update(kwargs)

    def clear_context(self) -> None:
        """Clear all context fields."""
        self.context.clear()

    @contextmanager
    def context_scope(
        self,
        batch_id: Optional[str] = None,
        source_type: Optional[str] = None,
        record_id: Optional[str] = None,
        status: Optional[str] = None,
        **kwargs: Any,
    ) -> Generator[None, None, None]:
        """Context manager for temporary context scope.

        Args:
            batch_id: Batch or run identifier.
            source_type: Data source type.
            record_id: Unique record identifier.
            status: Current processing status.
            **kwargs: Additional context fields.

        Yields:
            None. On exit, context is restored to previous state.
        """
        old_context = self.context.copy()

        try:
            self.set_context(
                batch_id=batch_id,
                source_type=source_type,
                record_id=record_id,
                status=status,
                **kwargs,
            )
            yield
        finally:
            self.context = old_context

    def _log_with_context(
        self,
        level: int,
        msg: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Internal method to log with context fields attached."""
        # Merge context into the log record
        for key, value in self.context.items():
            if value is not None:
                kwargs[key] = value

        self.logger.log(level, msg, *args, **kwargs)

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log a debug message."""
        self._log_with_context(logging.DEBUG, msg, *args, **kwargs)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log an info message."""
        self._log_with_context(logging.INFO, msg, *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log a warning message."""
        self._log_with_context(logging.WARNING, msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log an error message."""
        self._log_with_context(logging.ERROR, msg, *args, **kwargs)

    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log a critical message."""
        self._log_with_context(logging.CRITICAL, msg, *args, **kwargs)

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log an exception with traceback."""
        kwargs["exc_info"] = True
        self._log_with_context(logging.ERROR, msg, *args, **kwargs)


def get_logger(
    module_name: str,
    log_level: Optional[str] = None,
    log_output_path: Optional[str] = None,
) -> ContextualLogger:
    """Create and return a configured logger instance.

    Args:
        module_name: Name of the module requesting the logger (typically __name__).
        log_level: Override log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
                   If not provided, reads from LOG_LEVEL environment variable.
        log_output_path: Optional path to log file. If provided, logs to both
                        console and file.

    Returns:
        ContextualLogger instance ready for use.

    Raises:
        ValueError: If log_level is invalid.
    """
    logger = logging.getLogger(module_name)

    # Determine log level
    if log_level is None:
        log_level = os.getenv("LOG_LEVEL", "INFO")

    log_level = log_level.upper()

    try:
        level = getattr(logging, log_level)
    except AttributeError:
        raise ValueError(
            f"Invalid log level: {log_level}. "
            f"Must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL"
        )

    logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_formatter = StructuredFormatter(include_json=True)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # File handler (optional)
    if log_output_path:
        file_handler = logging.FileHandler(log_output_path)
        file_handler.setLevel(level)
        file_formatter = StructuredFormatter(include_json=True)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return ContextualLogger(logger)
