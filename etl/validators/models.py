from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from etl.validators.errors import ValidationErrorDetail
from etl.validators.schemas import SourceType, ValidatedRecord


class ValidationConfig(BaseModel):
    strict_mode: bool = False
    enable_quarantine: bool = True
    schema_version: str = "v1"
    max_errors_per_batch: int = 100
    log_level: str = "INFO"


class ValidationResult(BaseModel):
    accepted_records: List[ValidatedRecord]
    rejected_records: List[ValidationErrorDetail]
    summary: Dict[str, Any]
    raw_errors: List[ValidationErrorDetail]


class ValueValidationOutcome(BaseModel):
    record_id: str
    source_name: str
    source_type: SourceType
    valid: bool
    errors: List[ValidationErrorDetail]
    validated_record: Optional[ValidatedRecord]
    validated_at: datetime
