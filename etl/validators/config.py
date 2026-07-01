from __future__ import annotations

import os
from typing import Any

from etl.validators.models import ValidationConfig


def load_validation_config() -> ValidationConfig:
    strict_mode = os.getenv("VALIDATION_STRICT_MODE", "False").strip().lower() in {"1", "true", "yes", "on"}
    enable_quarantine = os.getenv("VALIDATION_ENABLE_QUARANTINE", "True").strip().lower() in {"1", "true", "yes", "on"}
    schema_version = os.getenv("VALIDATION_SCHEMA_VERSION", "v1")
    max_errors_per_batch = int(os.getenv("VALIDATION_MAX_ERRORS_PER_BATCH", "100"))
    log_level = os.getenv("VALIDATION_LOG_LEVEL", "INFO")

    return ValidationConfig(
        strict_mode=strict_mode,
        enable_quarantine=enable_quarantine,
        schema_version=schema_version,
        max_errors_per_batch=max_errors_per_batch,
        log_level=log_level,
    )
