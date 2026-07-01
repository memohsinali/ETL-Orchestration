from __future__ import annotations

import logging
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, EmailStr, Field, constr, field_validator

logger = logging.getLogger(__name__)


class SourceType(str, Enum):
    CSV = "csv"
    API = "api"
    MONGO = "mongo"


class BaseRecordSchema(BaseModel):
    source_name: str
    source_type: SourceType
    record_id: str
    ingestion_timestamp: datetime
    raw_record: Dict[str, Any]
    normalized_record: Dict[str, Any]


class UserRecordSchema(BaseModel):
    user_id: int = Field(..., ge=1)
    first_name: constr(strip_whitespace=True, min_length=1)
    last_name: constr(strip_whitespace=True, min_length=1)
    email: EmailStr
    phone: constr(strip_whitespace=True, min_length=7)
    gender: constr(strip_whitespace=True, min_length=1)
    date_of_birth: date
    city: constr(strip_whitespace=True, min_length=1)
    country: constr(strip_whitespace=True, min_length=1)
    address: constr(strip_whitespace=True, min_length=1)
    is_active: bool
    signup_source: constr(strip_whitespace=True, min_length=1)
    membership: constr(strip_whitespace=True, min_length=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def updated_not_before_created(cls, v: datetime, info: Any) -> datetime:
        created_at = (info.data or {}).get("created_at")
        if created_at and v < created_at:
            raise ValueError("updated_at must be equal to or after created_at")
        return v


# ---------------------------------------------------------------------------
# CSV-specific schema — matches csv_users.csv column layout exactly.
#
# Columns: user_id, full_name, email, phone, signup_date, country, age,
#          status, newsletter_opt_in
#
# Nullable / optional columns are modelled with Optional so that rows with
# missing values still pass validation rather than being wholesale rejected.
# ---------------------------------------------------------------------------

_VALID_STATUSES = {"active", "inactive", "suspended", "pending"}
_TRUTHY = {"yes", "y", "true", "1"}
_FALSY  = {"no",  "n", "false", "0"}


class CSVUserRecordSchema(BaseModel):
    """Validation schema for records sourced from csv_users.csv."""

    user_id: str = Field(..., min_length=1)
    full_name: str = Field(..., min_length=1)
    # email is required and must contain "@" — rows with blank or malformed
    # email are hard-rejected (e.g. CSV00024 which has no email at all,
    # CSV00104 / CSV00162 which also have blank emails).
    email: str = Field(..., min_length=1)
    # phone is optional — several rows have no phone
    phone: Optional[str] = None
    signup_date: date
    country: str = Field(..., min_length=1)
    # age is optional and may be -1 (sentinel for "unknown")
    age: Optional[int] = None
    status: str = Field(..., min_length=1)
    # newsletter_opt_in is optional — blank in many rows
    newsletter_opt_in: Optional[bool] = None

    # ------------------------------------------------------------------
    # Field-level coercions / validations
    # ------------------------------------------------------------------

    @field_validator("user_id", mode="before")
    @classmethod
    def strip_user_id(cls, v: Any) -> str:
        return str(v).strip()

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, v: Any) -> str:
        if v is None:
            raise ValueError("email is required")
        s = str(v).strip()
        if not s:
            raise ValueError("email must not be blank")
        if "@" not in s or "." not in s.split("@")[-1]:
            raise ValueError(f"email is malformed (missing @ or domain): {s!r}")
        return s

    @field_validator("phone", mode="before")
    @classmethod
    def normalise_phone(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @field_validator("signup_date", mode="before")
    @classmethod
    def parse_signup_date(cls, v: Any) -> date:
        if isinstance(v, date):
            return v
        s = str(v).strip()
        try:
            return date.fromisoformat(s)
        except ValueError:
            raise ValueError(f"signup_date must be YYYY-MM-DD, got: {s!r}")

    @field_validator("age", mode="before")
    @classmethod
    def normalise_age(cls, v: Any) -> Optional[int]:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        # Handle decimal-looking strings like "33.5" — truncate to int
        try:
            parsed = int(float(s))
        except ValueError:
            # Non-numeric garbage ("banana", "N/A") — sanitize to None with a warning
            logger.warning("age value %r is non-numeric, storing as NULL", s)
            return None
        # Non-positive values (-1 sentinel, other negatives) → None
        if parsed <= 0:
            logger.warning("age value %r is non-positive, storing as NULL", s)
            return None
        # Unrealistically high values → None
        if parsed > 150:
            logger.warning("age value %r exceeds maximum (150), storing as NULL", s)
            return None
        return parsed

    @field_validator("status", mode="before")
    @classmethod
    def normalise_status(cls, v: Any) -> str:
        s = str(v).strip().lower()
        if s not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {_VALID_STATUSES}, got: {s!r}")
        return s

    @field_validator("newsletter_opt_in", mode="before")
    @classmethod
    def parse_newsletter(cls, v: Any) -> Optional[bool]:
        if v is None:
            return None
        s = str(v).strip().lower()
        if not s:
            return None
        if s in _TRUTHY:
            return True
        if s in _FALSY:
            return False
        raise ValueError(f"newsletter_opt_in must be yes/no/true/false/y/n/1/0, got: {v!r}")


# ---------------------------------------------------------------------------
# API-specific schema — matches dummyjson.com /products response exactly.
#
# Every field that is always present is required. Nested objects (dimensions,
# meta) are flattened to Optional scalars so the validator stays simple and
# the loader can write flat columns.
# ---------------------------------------------------------------------------

class APIProductSchema(BaseModel):
    """Validation schema for product records from dummyjson.com/products."""

    id: int = Field(..., ge=1)
    title: str = Field(..., min_length=1)
    description: Optional[str] = None
    category: str = Field(..., min_length=1)
    price: float = Field(..., ge=0)
    discount_percentage: Optional[float] = None   # mapped from discountPercentage
    rating: Optional[float] = None
    stock: int = Field(..., ge=0)
    tags: Optional[list] = None
    brand: Optional[str] = None                   # some products have no brand
    sku: Optional[str] = None
    weight: Optional[float] = None
    # dimensions — flattened
    width: Optional[float] = None
    height: Optional[float] = None
    depth: Optional[float] = None
    warranty_information: Optional[str] = None    # mapped from warrantyInformation
    shipping_information: Optional[str] = None    # mapped from shippingInformation
    availability_status: Optional[str] = None     # mapped from availabilityStatus
    return_policy: Optional[str] = None           # mapped from returnPolicy
    minimum_order_quantity: Optional[int] = None  # mapped from minimumOrderQuantity
    # meta — flattened
    barcode: Optional[str] = None
    created_at: Optional[datetime] = None         # mapped from meta.createdAt
    updated_at: Optional[datetime] = None         # mapped from meta.updatedAt
    thumbnail: Optional[str] = None

    @field_validator("price", "rating", "discount_percentage", "weight",
                     "width", "height", "depth", mode="before")
    @classmethod
    def coerce_float(cls, v: Any) -> Optional[float]:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    @field_validator("stock", "minimum_order_quantity", mode="before")
    @classmethod
    def coerce_int(cls, v: Any) -> Optional[int]:
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def coerce_datetime(cls, v: Any) -> Optional[datetime]:
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        try:
            from datetime import timezone
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            return dt
        except ValueError:
            return None

    @field_validator("tags", mode="before")
    @classmethod
    def coerce_tags(cls, v: Any) -> Optional[list]:
        if v is None:
            return []
        if isinstance(v, list):
            return v
        return [str(v)]

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "APIProductSchema":
        """Flatten the nested dummyjson product dict before validation."""
        dims = raw.get("dimensions") or {}
        meta = raw.get("meta") or {}
        flat = {
            "id":                    raw.get("id"),
            "title":                 raw.get("title"),
            "description":           raw.get("description"),
            "category":              raw.get("category"),
            "price":                 raw.get("price"),
            "discount_percentage":   raw.get("discountPercentage"),
            "rating":                raw.get("rating"),
            "stock":                 raw.get("stock"),
            "tags":                  raw.get("tags"),
            "brand":                 raw.get("brand"),
            "sku":                   raw.get("sku"),
            "weight":                raw.get("weight"),
            "width":                 dims.get("width"),
            "height":                dims.get("height"),
            "depth":                 dims.get("depth"),
            "warranty_information":  raw.get("warrantyInformation"),
            "shipping_information":  raw.get("shippingInformation"),
            "availability_status":   raw.get("availabilityStatus"),
            "return_policy":         raw.get("returnPolicy"),
            "minimum_order_quantity": raw.get("minimumOrderQuantity"),
            "barcode":               meta.get("barcode"),
            "created_at":            meta.get("createdAt"),
            "updated_at":            meta.get("updatedAt"),
            "thumbnail":             raw.get("thumbnail"),
        }
        return cls(**flat)


# ---------------------------------------------------------------------------
# Mongo-specific schema — matches documents written by generate_mongo_users.py
#
# Fields: user_id, first_name, last_name, email, phone, gender,
#         date_of_birth, city, country, address, is_active,
#         signup_source, membership, created_at, updated_at
#
# The seed script stores date_of_birth as an ISO date string and
# created_at / updated_at as ISO datetime strings, so we coerce them.
# phone is stored as Faker phone_number output — accepted as-is.
# ---------------------------------------------------------------------------

_VALID_GENDERS    = {"m", "f", "other", "male", "female", "non-binary", "prefer not to say"}
_VALID_SOURCES    = {"web", "mobile_app", "partner", "referral"}
_VALID_MEMBERSHIPS = {"free", "basic", "premium", "enterprise"}


class MongoUserRecordSchema(BaseModel):
    """Validation schema for user documents from the MongoDB users collection."""

    user_id: int = Field(..., ge=1)
    first_name: str = Field(..., min_length=1)
    last_name: str = Field(..., min_length=1)
    email: str = Field(..., min_length=1)
    phone: Optional[str] = None
    gender: str = Field(..., min_length=1)
    date_of_birth: date
    city: str = Field(..., min_length=1)
    country: str = Field(..., min_length=1)
    address: str = Field(..., min_length=1)
    is_active: bool
    signup_source: str = Field(..., min_length=1)
    membership: str = Field(..., min_length=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("first_name", "last_name", "city", "country",
                     "address", "signup_source", "membership", mode="before")
    @classmethod
    def strip_string(cls, v: Any) -> str:
        if v is None:
            raise ValueError("field is required and must not be None")
        return str(v).strip()

    @field_validator("email", mode="before")
    @classmethod
    def validate_email(cls, v: Any) -> str:
        if v is None:
            raise ValueError("email is required")
        s = str(v).strip()
        if not s:
            raise ValueError("email must not be blank")
        if "@" not in s or "." not in s.split("@")[-1]:
            raise ValueError(f"email is malformed: {s!r}")
        return s

    @field_validator("phone", mode="before")
    @classmethod
    def normalise_phone(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @field_validator("gender", mode="before")
    @classmethod
    def normalise_gender(cls, v: Any) -> str:
        if v is None:
            raise ValueError("gender is required")
        s = str(v).strip()
        if not s:
            raise ValueError("gender must not be blank")
        # Accept any non-empty string — just strip and return as-is
        # (seed uses "M", "F", "Other" but real data may vary)
        return s

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def parse_dob(cls, v: Any) -> date:
        if isinstance(v, date) and not isinstance(v, datetime):
            return v
        if isinstance(v, datetime):
            return v.date()
        s = str(v).strip()
        try:
            return date.fromisoformat(s)
        except ValueError:
            raise ValueError(f"date_of_birth must be YYYY-MM-DD, got: {s!r}")

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def parse_datetime(cls, v: Any) -> datetime:
        if isinstance(v, datetime):
            return v
        s = str(v).strip()
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"datetime field must be ISO format, got: {s!r}")

    @field_validator("updated_at")
    @classmethod
    def updated_not_before_created(cls, v: datetime, info: Any) -> datetime:
        created_at = (info.data or {}).get("created_at")
        if created_at and v < created_at:
            raise ValueError("updated_at must be equal to or after created_at")
        return v

    @field_validator("is_active", mode="before")
    @classmethod
    def coerce_bool(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, int):
            return bool(v)
        s = str(v).strip().lower()
        if s in {"true", "1", "yes"}:
            return True
        if s in {"false", "0", "no"}:
            return False
        raise ValueError(f"is_active must be a boolean, got: {v!r}")


class ValidatedRecord(BaseModel):
    source_name: str
    source_type: SourceType
    record_id: str
    normalized_record: Dict[str, Any]
    validated_at: datetime
    schema_version: str


class ValidationErrorDetail(BaseModel):
    field_name: str
    message: str
    error_code: str
    context: Optional[Dict[str, Any]] = None


class ValidationOutcome(BaseModel):
    record_id: str
    source_name: str
    source_type: SourceType
    valid: bool
    validated_record: Optional[ValidatedRecord]
    errors: list[ValidationErrorDetail]


class ValidationSummary(BaseModel):
    source_name: str
    source_type: SourceType
    schema_version: str
    processed: int
    accepted: int
    rejected: int
    skipped: int
    errors: int
    validated_at: datetime
