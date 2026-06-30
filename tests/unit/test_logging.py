"""Unit tests for the logging module."""

import json
import logging
import os
import tempfile
from unittest.mock import patch

import pytest

from etl.utils.logging import (
    ContextualLogger,
    StructuredFormatter,
    get_logger,
    redact_sensitive_values,
)


class TestRedactSensitiveValues:
    """Tests for sensitive value redaction."""

    def test_redact_password_field(self):
        """Redact password field."""
        data = {"password": "secret123", "username": "john"}
        result = redact_sensitive_values(data)

        assert result["password"] == "[REDACTED]"
        assert result["username"] == "john"

    def test_redact_api_key_variants(self):
        """Redact various API key field name variants."""
        data = {
            "api_key": "key123",
            "apikey": "key456",
            "api-key": "key789",
            "API_KEY": "key000",
        }
        result = redact_sensitive_values(data)

        for key in data:
            assert result[key] == "[REDACTED]"

    def test_redact_token_and_secret(self):
        """Redact token and secret fields."""
        data = {
            "token": "abc123",
            "secret": "xyz789",
            "secret_key": "key123",
        }
        result = redact_sensitive_values(data)

        assert all(v == "[REDACTED]" for v in result.values())

    def test_preserve_non_sensitive_fields(self):
        """Preserve non-sensitive fields."""
        data = {
            "username": "john",
            "email": "john@example.com",
            "batch_id": "batch_001",
        }
        result = redact_sensitive_values(data)

        assert result == data


class TestStructuredFormatter:
    """Tests for structured log formatting."""

    def test_format_basic_log_as_json(self):
        """Format a basic log record as JSON."""
        formatter = StructuredFormatter(include_json=True)
        record = logging.LogRecord(
            name="test_module",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "INFO"
        assert data["module"] == "test_module"
        assert data["message"] == "Test message"
        assert "timestamp" in data

    def test_format_includes_context_fields(self):
        """Include custom context fields in formatted output."""
        formatter = StructuredFormatter(include_json=True)
        record = logging.LogRecord(
            name="test_module",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Processing record",
            args=(),
            exc_info=None,
        )
        record.batch_id = "batch_001"
        record.source_type = "csv"
        record.record_id = "rec_123"
        record.status = "success"

        output = formatter.format(record)
        data = json.loads(output)

        assert data["batch_id"] == "batch_001"
        assert data["source_type"] == "csv"
        assert data["record_id"] == "rec_123"
        assert data["status"] == "success"

    def test_format_redacts_sensitive_fields(self):
        """Redact sensitive fields in formatted output."""
        formatter = StructuredFormatter(include_json=True)
        record = logging.LogRecord(
            name="test_module",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Connecting to database",
            args=(),
            exc_info=None,
        )
        record.password = "secret123"
        record.api_key = "key456"

        output = formatter.format(record)
        data = json.loads(output)

        assert data["password"] == "[REDACTED]"
        assert data["api_key"] == "[REDACTED]"

    def test_format_as_key_value(self):
        """Format log record as key-value string."""
        formatter = StructuredFormatter(include_json=False)
        record = logging.LogRecord(
            name="test_module",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)

        assert "level=INFO" in output
        assert "module=test_module" in output
        assert "message=Test message" in output


class TestContextualLogger:
    """Tests for contextual logger wrapper."""

    def test_set_and_retrieve_context(self):
        """Set and verify context fields."""
        base_logger = logging.getLogger("test_contextual")
        contextual_logger = ContextualLogger(base_logger)

        contextual_logger.set_context(
            batch_id="batch_001",
            source_type="csv",
            record_id="rec_123",
        )

        assert contextual_logger.context["batch_id"] == "batch_001"
        assert contextual_logger.context["source_type"] == "csv"
        assert contextual_logger.context["record_id"] == "rec_123"

    def test_clear_context(self):
        """Clear all context fields."""
        base_logger = logging.getLogger("test_clear")
        contextual_logger = ContextualLogger(base_logger)

        contextual_logger.set_context(batch_id="batch_001")
        contextual_logger.clear_context()

        assert contextual_logger.context == {}

    def test_context_scope_manager(self):
        """Use context scope manager for temporary context."""
        base_logger = logging.getLogger("test_scope")
        contextual_logger = ContextualLogger(base_logger)

        contextual_logger.set_context(batch_id="outer")

        with contextual_logger.context_scope(batch_id="inner", source_type="csv"):
            assert contextual_logger.context["batch_id"] == "inner"
            assert contextual_logger.context["source_type"] == "csv"

        assert contextual_logger.context["batch_id"] == "outer"
        assert "source_type" not in contextual_logger.context

    def test_debug_info_warning_error_methods(self):
        """Test all logging level methods."""
        base_logger = logging.getLogger("test_levels")
        base_logger.handlers.clear()

        handler = logging.StreamHandler()
        base_logger.addHandler(handler)
        base_logger.setLevel(logging.DEBUG)

        contextual_logger = ContextualLogger(base_logger)

        # Should not raise
        contextual_logger.debug("Debug message")
        contextual_logger.info("Info message")
        contextual_logger.warning("Warning message")
        contextual_logger.error("Error message")
        contextual_logger.critical("Critical message")

    def test_exception_method_captures_traceback(self):
        """Test exception method with traceback."""
        base_logger = logging.getLogger("test_exception")
        base_logger.handlers.clear()

        handler = logging.StreamHandler()
        handler.setFormatter(StructuredFormatter(include_json=True))
        base_logger.addHandler(handler)
        base_logger.setLevel(logging.ERROR)

        contextual_logger = ContextualLogger(base_logger)

        try:
            raise ValueError("Test error")
        except ValueError:
            # Should not raise
            contextual_logger.exception("An error occurred")


class TestGetLogger:
    """Tests for the logger factory function."""

    def test_get_logger_default_configuration(self):
        """Create logger with default configuration."""
        logger = get_logger("test_default")

        assert isinstance(logger, ContextualLogger)
        assert logger.logger.name == "test_default"
        assert logger.logger.level in (logging.INFO, logging.NOTSET)

    def test_get_logger_with_explicit_log_level(self):
        """Create logger with explicit log level."""
        logger = get_logger("test_debug", log_level="DEBUG")

        assert logger.logger.level == logging.DEBUG

    def test_get_logger_reads_log_level_from_env(self):
        """Create logger reading log level from environment."""
        with patch.dict(os.environ, {"LOG_LEVEL": "WARNING"}):
            logger = get_logger("test_env")

        assert logger.logger.level == logging.WARNING

    def test_get_logger_invalid_log_level_raises_error(self):
        """Raise error for invalid log level."""
        with pytest.raises(ValueError, match="Invalid log level"):
            get_logger("test_invalid", log_level="INVALID")

    def test_get_logger_with_file_output(self):
        """Create logger that writes to file."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as tmp:
            log_path = tmp.name

        try:
            logger = get_logger("test_file", log_output_path=log_path)

            logger.info("Test log message")

            # Check that file was created and contains log
            with open(log_path, "r") as f:
                content = f.read()

            assert "Test log message" in content
            assert "test_file" in content
        finally:
            if os.path.exists(log_path):
                os.remove(log_path)

    def test_get_logger_case_insensitive_log_level(self):
        """Handle log level names in any case."""
        logger_upper = get_logger("test_upper", log_level="DEBUG")
        logger_lower = get_logger("test_lower", log_level="debug")

        assert logger_upper.logger.level == logging.DEBUG
        assert logger_lower.logger.level == logging.DEBUG

    def test_logger_handlers_cleared_on_creation(self):
        """Clear handlers when creating logger to avoid duplicates."""
        base_logger = logging.getLogger("test_handlers")
        base_logger.handlers.clear()

        # Create logger twice
        logger1 = get_logger("test_handlers", log_level="INFO")
        handler_count_1 = len(logger1.logger.handlers)

        logger2 = get_logger("test_handlers", log_level="INFO")
        handler_count_2 = len(logger2.logger.handlers)

        # Should have same number, not accumulated
        assert handler_count_1 == handler_count_2
