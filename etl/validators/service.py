from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from pydantic import ValidationError

from etl.validators.errors import ValidationErrorDetail
from etl.validators.models import ValidationConfig, ValidationResult
from etl.validators.registry import SchemaRegistry
from etl.validators.schemas import APIProductSchema, MongoUserRecordSchema, SourceType, ValidatedRecord

logger = logging.getLogger(__name__)


class ValidationService:
    def __init__(self, config: Optional[ValidationConfig] = None) -> None:
        self.config = config or ValidationConfig()
        self.registry = SchemaRegistry()

    def validate_record(self, record: Dict[str, Any], source_type: SourceType | str) -> tuple[Optional[ValidatedRecord], list[ValidationErrorDetail]]:
        if not isinstance(source_type, SourceType):
            source_type = SourceType(source_type)

        schema_version = self.config.schema_version
        schema_class = self.registry.resolve(source_type, schema_version)

        # For API products the extractor stores the raw camelCase product dict
        # inside normalized_record. Use APIProductSchema.from_raw() to flatten
        # and rename fields before validation.
        normalized_payload = record.get("normalized_record", {})
        if source_type == SourceType.API and schema_class is APIProductSchema:
            try:
                validated_payload = APIProductSchema.from_raw(normalized_payload)
            except ValidationError as exc:
                record_id = str(normalized_payload.get("id", "unknown"))
                source_name = str(record.get("source_name", "unknown"))
                errors = []
                for err in exc.errors():
                    loc = err.get("loc", ["unknown"])
                    errors.append(ValidationErrorDetail(
                        record_id=record_id,
                        source_name=source_name,
                        source_type=source_type.value,
                        field_name=".".join(str(x) for x in loc),
                        message=err.get("msg", "validation error"),
                        error_code=err.get("type", "validation_error"),
                        context={"raw_error": err},
                    ))
                return None, errors
            record_id = str(validated_payload.id)
            source_name = str(record.get("source_name", "unknown"))
            validated_record = ValidatedRecord(
                source_name=source_name,
                source_type=source_type,
                record_id=record_id,
                normalized_record=validated_payload.model_dump(),
                validated_at=datetime.now(timezone.utc),
                schema_version=schema_version,
            )
            return validated_record, []

        # All other source types (CSV, Mongo)
        # Resolve record_id: Mongo uses user_id, CSV uses user_id too
        record_id = str(
            record.get("record_id")
            or normalized_payload.get("user_id")
            or normalized_payload.get("id")
            or "unknown"
        )
        source_name = str(record.get("source_name", "unknown"))

        try:
            validated_payload = schema_class(**normalized_payload)
            validated_record = ValidatedRecord(
                source_name=source_name,
                source_type=source_type,
                record_id=record_id,
                normalized_record=validated_payload.model_dump(),
                validated_at=datetime.now(timezone.utc),
                schema_version=schema_version,
            )
            return validated_record, []
        except ValidationError as exc:
            errors = []
            for err in exc.errors():
                loc = err.get("loc", ["unknown"])
                field_name = ".".join(str(x) for x in loc)
                message = err.get("msg", "validation error")
                error_code = err.get("type", "validation_error")
                errors.append(
                    ValidationErrorDetail(
                        record_id=record_id,
                        source_name=source_name,
                        source_type=source_type.value,
                        field_name=field_name,
                        message=message,
                        error_code=error_code,
                        context={"raw_error": err},
                    )
                )
            return None, errors

    def validate_batch(self, records: Iterable[Dict[str, Any]], source_type: SourceType | str) -> ValidationResult:
        if not isinstance(source_type, SourceType):
            source_type = SourceType(source_type)
        accepted: list[ValidatedRecord] = []
        rejected: list[ValidationErrorDetail] = []
        skipped = 0
        records_list = list(records)

        for record in records_list:
            try:
                validated, errors = self.validate_record(record, source_type)
                if validated:
                    accepted.append(validated)
                else:
                    rejected.extend(errors)
            except Exception as exc:
                logger.error("Unexpected validation exception for record %s: %s", record.get("record_id"), exc)
                rejected.append(
                    ValidationErrorDetail(
                        record_id=str(record.get("record_id", "unknown")),
                        source_name=str(record.get("source_name", "unknown")),
                        source_type=source_type.value,
                        field_name="__batch__",
                        message=str(exc),
                        error_code="unexpected_exception",
                    )
                )

        summary = {
            "source_name": accepted[0].source_name if accepted else (rejected[0].source_name if rejected else "unknown"),
            "source_type": source_type.value,
            "schema_version": self.config.schema_version,
            "processed": len(records_list),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "skipped": skipped,
            "errors": len(rejected),
            "validated_at": datetime.now(timezone.utc),
        }

        return ValidationResult(
            accepted_records=accepted,
            rejected_records=rejected,
            summary=summary,
            raw_errors=rejected,
        )
