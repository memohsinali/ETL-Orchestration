from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from etl.validators.schemas import CSVUserRecordSchema, SourceType, ValidatedRecord

logger = logging.getLogger(__name__)


class TransformationError(Exception):
    """Raised when a validated record cannot be transformed safely."""


@dataclass(frozen=True)
class CanonicalRecord:
    record_id: str
    source_name: str
    source_type: str
    canonical_record: dict[str, Any]
    transformed_at: datetime
    schema_version: str = "v1"


@dataclass
class TransformationResult:
    transformed_records: list[CanonicalRecord] = field(default_factory=list)
    rejected_records: list[TransformationError] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


class DataTransformer:
    """Transform validated records into a canonical, loader-ready shape."""

    def __init__(self, strict_mode: bool = False) -> None:
        self.strict_mode = strict_mode

    # ------------------------------------------------------------------
    # CSV  →  etl_csv_users
    # ------------------------------------------------------------------

    def _transform_csv_record(
        self, normalized: dict[str, Any], record: ValidatedRecord
    ) -> dict[str, Any]:
        """Map csv_users.csv fields to the canonical shape for etl_csv_users."""
        CSVUserRecordSchema(**normalized)

        age    = normalized.get("age")
        status = str(normalized.get("status", "")).strip().lower()

        return {
            "user_id":          str(normalized.get("user_id", "")).strip(),
            "full_name":        str(normalized.get("full_name", "")).strip(),
            "email":            str(normalized.get("email", "")).strip() or None,
            "phone":            str(normalized.get("phone", "")).strip() or None,
            "signup_date":      str(normalized.get("signup_date", "")),
            "country":          str(normalized.get("country", "")).strip(),
            "age":              age,
            "status":           status,
            "is_active":        status == "active",
            "newsletter_opt_in": normalized.get("newsletter_opt_in"),
            "source":           record.source_name,
            "source_type":      self._source_type_value(record),
            "validated_at":     record.validated_at.isoformat() if record.validated_at else None,
        }

    # ------------------------------------------------------------------
    # Mongo  →  etl_mongo_users
    # ------------------------------------------------------------------

    def _transform_mongo_record(
        self, normalized: dict[str, Any], record: ValidatedRecord
    ) -> dict[str, Any]:
        """Map Mongo user document fields to the canonical shape for etl_mongo_users."""
        return {
            "user_id":       int(normalized.get("user_id")),
            "first_name":    str(normalized.get("first_name", "")).strip(),
            "last_name":     str(normalized.get("last_name", "")).strip(),
            "full_name":     (
                f"{str(normalized.get('first_name', '')).strip()} "
                f"{str(normalized.get('last_name', '')).strip()}"
            ).strip(),
            "email":         str(normalized.get("email", "")).strip(),
            "phone":         str(normalized.get("phone", "")).strip() or None,
            "gender":        str(normalized.get("gender", "")).strip(),
            "date_of_birth": str(normalized.get("date_of_birth", "")),
            "city":          str(normalized.get("city", "")).strip(),
            "country":       str(normalized.get("country", "")).strip(),
            "address":       str(normalized.get("address", "")).strip(),
            "is_active":     bool(normalized.get("is_active", False)),
            "signup_source": str(normalized.get("signup_source", "")).strip(),
            "membership":    str(normalized.get("membership", "")).strip(),
            "created_at":    str(normalized.get("created_at", "")),
            "updated_at":    str(normalized.get("updated_at", "")),
            "source":        record.source_name,
            "source_type":   self._source_type_value(record),
            "validated_at":  record.validated_at.isoformat() if record.validated_at else None,
        }

    # ------------------------------------------------------------------
    # API  →  etl_api_products
    # ------------------------------------------------------------------

    def _transform_api_record(
        self, normalized: dict[str, Any], record: ValidatedRecord
    ) -> dict[str, Any]:
        """Map dummyjson product fields to the canonical shape for etl_api_products."""
        return {
            "product_id":             normalized.get("id"),
            "title":                  str(normalized.get("title", "")).strip(),
            "description":            normalized.get("description"),
            "category":               str(normalized.get("category", "")).strip(),
            "price":                  normalized.get("price"),
            "discount_percentage":    normalized.get("discount_percentage"),
            "rating":                 normalized.get("rating"),
            "stock":                  normalized.get("stock"),
            "tags":                   ",".join(normalized.get("tags") or []),
            "brand":                  normalized.get("brand"),
            "sku":                    normalized.get("sku"),
            "weight":                 normalized.get("weight"),
            "width":                  normalized.get("width"),
            "height":                 normalized.get("height"),
            "depth":                  normalized.get("depth"),
            "warranty_information":   normalized.get("warranty_information"),
            "shipping_information":   normalized.get("shipping_information"),
            "availability_status":    normalized.get("availability_status"),
            "in_stock":               (
                (normalized.get("availability_status") or "").lower() != "out of stock"
            ),
            "return_policy":          normalized.get("return_policy"),
            "minimum_order_quantity": normalized.get("minimum_order_quantity"),
            "barcode":                normalized.get("barcode"),
            "created_at":             str(normalized.get("created_at", "")),
            "updated_at":             str(normalized.get("updated_at", "")),
            "thumbnail":              normalized.get("thumbnail"),
            "source":                 record.source_name,
            "source_type":            self._source_type_value(record),
            "validated_at":           record.validated_at.isoformat() if record.validated_at else None,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _source_type_value(self, record: ValidatedRecord) -> str:
        return (
            record.source_type.value
            if isinstance(record.source_type, SourceType)
            else str(record.source_type)
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def transform_record(self, record: ValidatedRecord) -> CanonicalRecord:
        normalized = record.normalized_record or {}
        source_type = (
            record.source_type
            if isinstance(record.source_type, SourceType)
            else SourceType(record.source_type)
        )

        try:
            if source_type == SourceType.CSV:
                canonical = self._transform_csv_record(normalized, record)
            elif source_type == SourceType.MONGO:
                canonical = self._transform_mongo_record(normalized, record)
            else:  # API
                canonical = self._transform_api_record(normalized, record)

            return CanonicalRecord(
                record_id=str(record.record_id),
                source_name=record.source_name,
                source_type=source_type.value,
                canonical_record=canonical,
                transformed_at=datetime.now(timezone.utc),
                schema_version=record.schema_version,
            )
        except Exception as exc:
            logger.error(
                "Transformation failed for record %s: %s", record.record_id, exc
            )
            raise TransformationError(
                f"Unable to transform record {record.record_id}: {exc}"
            ) from exc

    def transform_batch(self, records: list[ValidatedRecord]) -> TransformationResult:
        transformed: list[CanonicalRecord] = []
        rejected:    list[TransformationError] = []

        for record in records:
            try:
                transformed.append(self.transform_record(record))
            except TransformationError as exc:
                rejected.append(exc)
                if self.strict_mode:
                    raise

        summary = {
            "processed":      len(records),
            "accepted":       len(transformed),
            "rejected":       len(rejected),
            "strict_mode":    self.strict_mode,
            "transformed_at": datetime.now(timezone.utc),
        }
        return TransformationResult(
            transformed_records=transformed,
            rejected_records=rejected,
            summary=summary,
        )
