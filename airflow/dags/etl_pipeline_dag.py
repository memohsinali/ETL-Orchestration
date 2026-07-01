from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.decorators import task

from etl.extractors.api_extractor import APIExtractor, APIExtractorConfig
from etl.extractors.csv_extractor import CSVExtractor, CSVExtractorConfig
from etl.extractors.mongo_extractor import MongoExtractor, MongoExtractorConfig
from etl.loaders.postgres_loader import PostgresLoader
from etl.transformers import DataTransformer
from etl.validators.service import ValidationService
from etl.validators.schemas import SourceType, ValidatedRecord


def _extract_csv() -> list[dict]:
    extractor = CSVExtractor(
        CSVExtractorConfig(
            input_path="data",
            source_name="csv_users",
            source_id_field="user_id",
        )
    )
    result = extractor.extract()
    if result.errors:
        raise RuntimeError(f"CSV extraction reported errors: {result.errors}")
    return result.records


def _extract_api() -> list[dict]:
    config = APIExtractorConfig(
        base_url="https://dummyjson.com",
        endpoint="products",
        pagination_style="offset",
        page_param="skip",
        page_size_param="limit",
        page_size=30,
        start_page=0,
        records_path="products",
        total_path="total",
        source_name="api_products",
        source_id_field="id",
    )
    extractor = APIExtractor(config)
    result = extractor.extract()
    if result.errors:
        raise RuntimeError(f"API extraction reported errors: {result.errors}")
    return result.records


def _extract_mongo() -> list[dict]:
    extractor = MongoExtractor(
        MongoExtractorConfig(
            collection="users",
            source_name="mongo_users",
            source_id_field="user_id",   # business key, not _id
        )
    )
    result = extractor.extract()
    if result.errors:
        raise RuntimeError(f"Mongo extraction reported errors: {result.errors}")
    return result.records


def _validate_records(raw_records: list[dict], source_type: str) -> list[dict]:
    service = ValidationService()
    validated_records: list[dict] = []
    # Airflow passes task arguments as plain strings between tasks,
    # so source_type arrives as a str (e.g. "api"), not a SourceType enum.
    source_type_str = source_type if isinstance(source_type, str) else source_type.value

    for record in raw_records:
        # CSV and Mongo extractors wrap records in the normalized_record envelope.
        # API extractor returns raw product dicts — wrap them here.
        if "normalized_record" in record:
            wrapped = record
        else:
            wrapped = {
                "normalized_record": record,
                "source_name": f"{source_type_str}_source",
            }
        validated, errors = service.validate_record(wrapped, source_type_str)
        if validated:
            validated_records.append(validated.model_dump())

    return validated_records


def _transform_records(validated_records: list[dict]) -> list[dict]:
    transformer = DataTransformer()
    records = [ValidatedRecord.model_validate(record) for record in validated_records]
    result = transformer.transform_batch(records)

    if result.rejected_records:
        raise RuntimeError(f"Transformation rejected {len(result.rejected_records)} records")

    return [
        {
            "record_id":       record.record_id,
            "source_name":     record.source_name,
            "source_type":     record.source_type if isinstance(record.source_type, str) else record.source_type.value,
            "canonical_record": record.canonical_record,
            "schema_version":  record.schema_version,
        }
        for record in result.transformed_records
    ]


def _load_records(transformed_records: list[dict]) -> None:
    loader = PostgresLoader(table="etl_users", id_column="record_id", source_name="etl_pipeline")
    loader.load(transformed_records)


def _load_csv_records(transformed_records: list[dict]) -> None:
    """Load CSV-sourced records into the dedicated etl_csv_users table."""
    loader = PostgresLoader(
        table="etl_csv_users",
        id_column="user_id",
        source_name="csv_users",
        auto_create_table=True,
    )
    loader.load(transformed_records)


def _load_api_product_records(transformed_records: list[dict]) -> None:
    """Load API product records into the dedicated etl_api_products table."""
    loader = PostgresLoader(
        table="etl_api_products",
        id_column="product_id",
        source_name="api_products",
        auto_create_table=True,
    )
    loader.load(transformed_records)


def _load_mongo_records(transformed_records: list[dict]) -> None:
    """Load Mongo user records into the dedicated etl_mongo_users table."""
    loader = PostgresLoader(
        table="etl_mongo_users",
        id_column="user_id",
        source_name="mongo_users",
        auto_create_table=True,
    )
    loader.load(transformed_records)


with DAG(
    dag_id="etl_pipeline_dag",
    start_date=datetime(2024, 1, 1),
    schedule_interval=None,
    catchup=False,
    default_args={
        "owner": "etl",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "depends_on_past": False,
    },
    tags=["etl", "spec-010", "parallel"],
) as dag:
    # ------------------------------------------------------------------ #
    # Extract                                                              #
    # ------------------------------------------------------------------ #
    csv_records   = task(task_id="extract_csv")(_extract_csv)()
    api_records   = task(task_id="extract_api")(_extract_api)()
    mongo_records = task(task_id="extract_mongo")(_extract_mongo)()

    # ------------------------------------------------------------------ #
    # Validate (each source uses its own registered schema)               #
    # ------------------------------------------------------------------ #
    csv_validated   = task(task_id="validate_csv")(_validate_records)(csv_records,   SourceType.CSV.value)
    api_validated   = task(task_id="validate_api")(_validate_records)(api_records,   SourceType.API.value)
    mongo_validated = task(task_id="validate_mongo")(_validate_records)(mongo_records, SourceType.MONGO.value)

    # ------------------------------------------------------------------ #
    # Transform                                                            #
    # ------------------------------------------------------------------ #
    csv_transformed   = task(task_id="transform_csv")(_transform_records)(csv_validated)
    api_transformed   = task(task_id="transform_api")(_transform_records)(api_validated)
    mongo_transformed = task(task_id="transform_mongo")(_transform_records)(mongo_validated)

    # ------------------------------------------------------------------ #
    # Load                                                                 #
    # CSV   → etl_csv_users                                               #
    # API   → etl_api_products                                            #
    # Mongo → etl_users  (unchanged, uses existing UserRecordSchema)      #
    # ------------------------------------------------------------------ #
    task(task_id="load_csv")(_load_csv_records)(csv_transformed)
    task(task_id="load_api")(_load_api_product_records)(api_transformed)
    task(task_id="load_mongo")(_load_records)(mongo_transformed)
