from __future__ import annotations

from datetime import datetime, timezone

from etl.transformers import DataTransformer, TransformationError
from etl.validators.schemas import SourceType, ValidatedRecord


def make_mongo_record(record_id: str = "42", email: str = "ada@example.com") -> ValidatedRecord:
    return ValidatedRecord(
        source_name="mongo-source",
        source_type=SourceType.MONGO,
        record_id=record_id,
        normalized_record={
            "user_id": int(record_id),
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": email,
            "phone": "1234567890",
            "gender": "F",
            "date_of_birth": "1990-01-01",
            "city": "London",
            "country": "UK",
            "address": "10 Downing St",
            "is_active": True,
            "signup_source": "web",
            "membership": "premium",
            "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(),
            "updated_at": datetime(2024, 1, 2, tzinfo=timezone.utc).isoformat(),
        },
        validated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        schema_version="v1",
    )


def make_api_record(record_id: str = "1") -> ValidatedRecord:
    return ValidatedRecord(
        source_name="api-source",
        source_type=SourceType.API,
        record_id=record_id,
        normalized_record={
            "id": int(record_id),
            "title": "Test Product",
            "description": "A test product",
            "category": "electronics",
            "price": 9.99,
            "discount_percentage": 5.0,
            "rating": 4.5,
            "stock": 100,
            "tags": ["test", "product"],
            "brand": "TestBrand",
            "sku": "TEST-001",
            "weight": 1.0,
            "width": 10.0,
            "height": 10.0,
            "depth": 5.0,
            "warranty_information": "1 year",
            "shipping_information": "Ships in 1-2 days",
            "availability_status": "In Stock",
            "return_policy": "30 days",
            "minimum_order_quantity": 1,
            "barcode": "123456789",
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-02T00:00:00+00:00",
            "thumbnail": "https://example.com/thumb.jpg",
        },
        validated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        schema_version="v1",
    )


def test_transform_mongo_record_creates_canonical_payload():
    transformer = DataTransformer()
    record = make_mongo_record("42")

    transformed = transformer.transform_record(record)

    assert transformed.record_id == "42"
    assert transformed.source_name == "mongo-source"
    assert transformed.canonical_record["user_id"] == 42
    assert transformed.canonical_record["full_name"] == "Ada Lovelace"
    assert transformed.canonical_record["email"] == "ada@example.com"
    assert transformed.canonical_record["country"] == "UK"
    assert transformed.canonical_record["is_active"] is True
    assert transformed.canonical_record["membership"] == "premium"


def test_transform_api_record_creates_canonical_payload():
    transformer = DataTransformer()
    record = make_api_record("1")

    transformed = transformer.transform_record(record)

    assert transformed.record_id == "1"
    assert transformed.source_name == "api-source"
    assert transformed.canonical_record["product_id"] == 1
    assert transformed.canonical_record["title"] == "Test Product"
    assert transformed.canonical_record["category"] == "electronics"
    assert transformed.canonical_record["in_stock"] is True
    assert transformed.canonical_record["tags"] == "test,product"


def test_transform_batch_returns_summary_and_rejections():
    transformer = DataTransformer(strict_mode=False)
    valid   = make_mongo_record("42")
    # Provide a bad user_id (None) so the transformer raises when building int(None)
    invalid = make_mongo_record("99")
    invalid.normalized_record["user_id"] = None

    result = transformer.transform_batch([valid, invalid])

    assert result.summary["accepted"] == 1
    assert result.summary["rejected"] == 1
    assert len(result.transformed_records) == 1
    assert len(result.rejected_records) == 1
    assert isinstance(result.rejected_records[0], TransformationError)
