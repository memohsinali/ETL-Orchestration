"""Central configuration loader for the ETL orchestrator.

This module loads environment variables, validates them, and exposes a
strongly-typed Settings object for use throughout the application.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Application settings."""

    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_host: str
    postgres_port: int

    mongo_uri: str
    mongo_db: str

    airflow_executor: str
    airflow_load_examples: bool

    log_level: str

    validation_strict_mode: bool
    validation_enable_quarantine: bool


class MissingConfigurationError(ValueError):
    """Raised when a required configuration value is missing."""


class InvalidConfigurationError(ValueError):
    """Raised when a configuration value is invalid."""


def _get_required_setting(name: str, default: Optional[str] = None) -> str:
    """Return a required environment variable."""
    value = os.getenv(name, default)

    if value is None or str(value).strip() == "":
        raise MissingConfigurationError(
            f"Required configuration value is missing: {name}"
        )

    return str(value)


def _get_bool_setting(name: str, default: bool) -> bool:
    """Return a boolean environment variable."""
    value = os.getenv(name)

    if value is None:
        return default

    normalized = value.strip().lower()

    if normalized in {"1", "true", "yes", "on"}:
        return True

    if normalized in {"0", "false", "no", "off"}:
        return False

    raise InvalidConfigurationError(
        f"Invalid boolean value for {name}: {value}"
    )


def _get_int_setting(name: str, default: int) -> int:
    """Return an integer environment variable."""
    value = os.getenv(name)

    if value is None:
        return default

    try:
        return int(value)
    except ValueError as exc:
        raise InvalidConfigurationError(
            f"Invalid integer value for {name}: {value}"
        ) from exc


def load_settings() -> Settings:
    """Load and validate application settings."""

    return Settings(
        postgres_db=_get_required_setting("POSTGRES_DB", "etl_db"),
        postgres_user=_get_required_setting("POSTGRES_USER", "etl_user"),

        # Intentionally required
        postgres_password=_get_required_setting("POSTGRES_PASSWORD"),

        postgres_host=_get_required_setting("POSTGRES_HOST", "postgres"),
        postgres_port=_get_int_setting("POSTGRES_PORT", 5432),

        # Intentionally required
        mongo_uri=_get_required_setting("MONGO_URI"),

        mongo_db=_get_required_setting("MONGO_DB", "etl_db"),

        airflow_executor=_get_required_setting(
            "AIRFLOW__CORE__EXECUTOR",
            "LocalExecutor",
        ),

        airflow_load_examples=_get_bool_setting(
            "AIRFLOW__CORE__LOAD_EXAMPLES",
            False,
        ),

        log_level=os.getenv("LOG_LEVEL", "INFO"),

        validation_strict_mode=_get_bool_setting(
            "VALIDATION_STRICT_MODE",
            False,
        ),

        validation_enable_quarantine=_get_bool_setting(
            "VALIDATION_ENABLE_QUARANTINE",
            True,
        ),
    )