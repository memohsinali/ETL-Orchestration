from __future__ import annotations

from datetime import datetime, timezone

from etl.validators.models import ValidationConfig
from etl.validators.schemas import SourceType
from etl.validators.service import ValidationService


# ---------------------------------------------------------------------------
# Helpers — one builder per source type
# ---------------------------------------------------------------------------

def make_mongo_record(email: str = "alice.smith@example.com") -> dict:
    """Wraps a Mongo user document in the normalized_record envelope."""
    return {
        "source_name": "test_mongo",
        "normalized_record": {
            "user_id": 1,
            "first_name": "Alice",
            "last_name": "Smith",
            "email": email,
            "phone": "1234567890",
            "gender": "F",
            "date_of_birth": "1990-01-01",
            "city": "Seattle",
            "country": "USA",
            "address": "123 Main St",
            "is_active": True,
            "signup_source": "web",
            "membership": "premium",
            "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(),
            "updated_at": datetime(2024, 1, 2, tzinfo=timezone.utc).isoformat(),
        },
    }


def make_api_record(price: float = 9.99) -> dict:
    """Raw product dict from dummyjson — NOT wrapped (API extractor returns raw dicts)."""
    return {
        "id": 1,
        "title": "Test Product",
        "description": "A product for testing",
        "category": "electronics",
        "price": price,
        "discountPercentage": 5.0,
        "rating": 4.5,
        "stock": 50,
        "tags": ["test"],
        "brand": "TestBrand",
        "sku": "TEST-001",
        "weight": 1.0,
        "dimensions": {"width": 10.0, "height": 5.0, "depth": 3.0},
        "warrantyInformation": "1 year",
        "shippingInformation": "Ships in 1-2 days",
        "availabilityStatus": "In Stock",
        "returnPolicy": "30 days",
        "minimumOrderQuantity": 1,
        "meta": {
            "barcode": "123456",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-01-02T00:00:00.000Z",
        },
        "thumbnail": "https://example.com/thumb.jpg",
    }


# ---------------------------------------------------------------------------
# Mongo validation tests
# ---------------------------------------------------------------------------

def test_validate_mongo_record_success():
    service = ValidationService(config=ValidationConfig(schema_version="v1"))
    record = make_mongo_record()

    validated, errors = service.validate_record(record, SourceType.MONGO)

    assert validated is not None
    assert validated.record_id == "1"
    assert not errors


def test_validate_mongo_record_failure():
    service = ValidationService(config=ValidationConfig(schema_version="v1"))
    record = make_mongo_record(email="not-an-email")

    validated, errors = service.validate_record(record, SourceType.MONGO)

    assert validated is None
    assert len(errors) == 1
    assert errors[0].field_name == "email"


def test_validate_mongo_batch():
    service = ValidationService(config=ValidationConfig(schema_version="v1"))
    valid   = make_mongo_record()
    invalid = make_mongo_record(email="bad-email")

    result = service.validate_batch([valid, invalid], SourceType.MONGO)

    assert result.summary["processed"] == 2
    assert result.summary["accepted"] == 1
    assert result.summary["rejected"] == 1
    assert len(result.accepted_records) == 1
    assert len(result.rejected_records) == 1


# ---------------------------------------------------------------------------
# API validation tests (uses APIProductSchema + from_raw flattening)
# ---------------------------------------------------------------------------

def test_validate_api_record_success():
    service = ValidationService(config=ValidationConfig(schema_version="v1"))
    # API records arrive unwrapped — service wraps them internally via the DAG,
    # but for direct service calls we wrap them here the same way the DAG does.
    record = {
        "normalized_record": make_api_record(),
        "source_name": "api_source",
    }

    validated, errors = service.validate_record(record, SourceType.API)

    assert validated is not None
    assert validated.record_id == "1"
    assert not errors


def test_validate_api_record_failure():
    service = ValidationService(config=ValidationConfig(schema_version="v1"))
    raw = make_api_record()
    raw["price"] = -1.0   # price must be >= 0 — negative should fail
    record = {"normalized_record": raw, "source_name": "api_source"}

    validated, errors = service.validate_record(record, SourceType.API)

    assert validated is None
    assert len(errors) >= 1


def test_validate_api_batch():
    service = ValidationService(config=ValidationConfig(schema_version="v1"))
    valid_raw   = make_api_record()
    invalid_raw = make_api_record()
    invalid_raw["title"] = ""   # title min_length=1 → should fail

    valid   = {"normalized_record": valid_raw,   "source_name": "api_source"}
    invalid = {"normalized_record": invalid_raw, "source_name": "api_source"}

    result = service.validate_batch([valid, invalid], SourceType.API)

    assert result.summary["processed"] == 2
    assert result.summary["accepted"] == 1
    assert result.summary["rejected"] == 1


# ---------------------------------------------------------------------------
# Keep the original names as aliases so any runner using the old names passes
# ---------------------------------------------------------------------------

test_validate_record_success = test_validate_mongo_record_success
test_validate_record_failure = test_validate_mongo_record_failure
test_validate_batch          = test_validate_mongo_batch
