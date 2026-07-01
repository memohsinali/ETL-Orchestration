from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

from etl.extractors.api_extractor import APIExtractor, APIExtractorConfig
from etl.extractors.csv_extractor import CSVExtractor, CSVExtractorConfig
from etl.extractors.mongo_extractor import MongoExtractor, MongoExtractorConfig
from etl.loaders.postgres_loader import PostgresLoader
from etl.transformers import DataTransformer
from etl.validators.service import ValidationService
from etl.validators.schemas import SourceType, ValidatedRecord


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------

def extract_csv_task() -> list[dict]:
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


def extract_api_task() -> list[dict]:
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


def extract_mongo_task() -> list[dict]:
    extractor = MongoExtractor(
        MongoExtractorConfig(
            collection="users",
            source_name="mongo_users",
            source_id_field="user_id",
        )
    )
    result = extractor.extract()
    if result.errors:
        raise RuntimeError(f"Mongo extraction reported errors: {result.errors}")
    return result.records


# ---------------------------------------------------------------------------
# Validate — merges all three sources, each with its own schema
# ---------------------------------------------------------------------------

def validate_task(**context) -> list:
    ti = context["ti"]
    csv_records   = ti.xcom_pull(task_ids="extract_csv")   or []
    api_records   = ti.xcom_pull(task_ids="extract_api")   or []
    mongo_records = ti.xcom_pull(task_ids="extract_mongo") or []

    service = ValidationService()
    validated_records = []

    # source_type is passed as a plain string to stay XCom-safe
    sources = [
        (csv_records,   SourceType.CSV.value),
        (api_records,   SourceType.API.value),
        (mongo_records, SourceType.MONGO.value),
    ]

    for raw_records, source_type_str in sources:
        if not isinstance(raw_records, list):
            raw_records = [raw_records] if raw_records else []

        for record in raw_records:
            if not isinstance(record, dict):
                continue

            # CSV and Mongo extractors wrap records in the normalized_record
            # envelope. API extractor returns raw product dicts — wrap them.
            if "normalized_record" in record:
                wrapped = record
            else:
                wrapped = {
                    "normalized_record": record,
                    "source_name": f"{source_type_str}_source",
                }

            validated, errors = service.validate_record(wrapped, source_type_str)
            if validated:
                validated_records.append(validated)

    if not validated_records:
        raise RuntimeError("No validated records produced")

    return [r.model_dump() for r in validated_records]


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------

def transform_task(**context) -> list:
    ti = context["ti"]
    validated_records = ti.xcom_pull(task_ids="validate") or []

    records = [ValidatedRecord.model_validate(r) for r in validated_records]
    transformer = DataTransformer()
    result = transformer.transform_batch(records)

    if result.rejected_records:
        raise RuntimeError(
            f"Transformation rejected {len(result.rejected_records)} records"
        )

    return [
        {
            "record_id":       record.record_id,
            "source_name":     record.source_name,
            # source_type is already a str on CanonicalRecord — no .value needed
            "source_type":     record.source_type if isinstance(record.source_type, str)
                               else record.source_type.value,
            "canonical_record": record.canonical_record,
            "schema_version":  record.schema_version,
        }
        for record in result.transformed_records
    ]


# ---------------------------------------------------------------------------
# Load — routes each record to its own table based on source_type
# ---------------------------------------------------------------------------

def load_task(**context) -> None:
    ti = context["ti"]
    transformed_records = ti.xcom_pull(task_ids="transform") or []

    csv_records   = [r for r in transformed_records if r.get("source_type") == "csv"]
    api_records   = [r for r in transformed_records if r.get("source_type") == "api"]
    mongo_records = [r for r in transformed_records if r.get("source_type") == "mongo"]

    if csv_records:
        PostgresLoader(
            table="etl_csv_users",
            id_column="user_id",
            source_name="csv_users",
            auto_create_table=True,
        ).load(csv_records)

    if api_records:
        PostgresLoader(
            table="etl_api_products",
            id_column="product_id",
            source_name="api_products",
            auto_create_table=True,
        ).load(api_records)

    if mongo_records:
        PostgresLoader(
            table="etl_mongo_users",
            id_column="user_id",
            source_name="mongo_users",
            auto_create_table=True,
        ).load(mongo_records)


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------

with DAG(
    dag_id="etl_parallel_pipeline_dag",
    start_date=datetime(2024, 1, 1),
    schedule_interval=None,
    catchup=False,
    default_args={
        "owner": "etl",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "depends_on_past": False,
    },
    tags=["etl", "parallel"],
) as dag:
    extract_csv   = PythonOperator(task_id="extract_csv",   python_callable=extract_csv_task)
    extract_api   = PythonOperator(task_id="extract_api",   python_callable=extract_api_task)
    extract_mongo = PythonOperator(task_id="extract_mongo", python_callable=extract_mongo_task)

    validate = PythonOperator(
        task_id="validate",
        python_callable=validate_task,
        provide_context=True,
    )

    transform = PythonOperator(
        task_id="transform",
        python_callable=transform_task,
        provide_context=True,
    )

    load = PythonOperator(
        task_id="load",
        python_callable=load_task,
        provide_context=True,
    )

    [extract_csv, extract_api, extract_mongo] >> validate >> transform >> load
