from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel


class ValidationException(Exception):
    pass


class SchemaResolutionError(ValidationException):
    pass


class InvalidConfigurationError(ValidationException):
    pass


class ValidationErrorDetail(BaseModel):
    record_id: str
    source_name: str
    source_type: str
    field_name: str
    message: str
    error_code: str
    context: Optional[Dict[str, Any]] = None
