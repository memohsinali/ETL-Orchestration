"""Database helper utilities for metadata and simple upserts.

This module provides lightweight helpers to interact with PostgreSQL for
checkpointing ETL runs and performing upserts required for idempotent loads.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable, Optional, Set

import psycopg2
import psycopg2.extras

from config.settings.config import load_settings


def _connect():
    s = load_settings()
    return psycopg2.connect(
        dbname=s.postgres_db,
        user=s.postgres_user,
        password=s.postgres_password,
        host=s.postgres_host,
        port=s.postgres_port,
    )


def ensure_etl_metadata_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS etl_metadata (
                source_name TEXT PRIMARY KEY,
                last_successful_run TIMESTAMPTZ,
                last_processed_id TEXT,
                last_processed_timestamp TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    conn.commit()


def ensure_mongo_users_table(conn) -> None:
    """Create the etl_mongo_users table if it does not already exist.

    Column names mirror the canonical record produced by
    DataTransformer._transform_mongo_record().
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS etl_mongo_users (
                user_id         INTEGER PRIMARY KEY,
                first_name      TEXT,
                last_name       TEXT,
                full_name       TEXT,
                email           TEXT,
                phone           TEXT,
                gender          TEXT,
                date_of_birth   TEXT,
                city            TEXT,
                country         TEXT,
                address         TEXT,
                is_active       BOOLEAN,
                signup_source   TEXT,
                membership      TEXT,
                created_at      TEXT,
                updated_at      TEXT,
                source          TEXT,
                source_type     TEXT,
                validated_at    TEXT,
                etl_updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    conn.commit()


def ensure_api_products_table(conn) -> None:
    """Create or migrate the etl_api_products table.

    Uses CREATE TABLE IF NOT EXISTS for the initial creation, then
    ALTER TABLE ... ADD COLUMN IF NOT EXISTS for any columns that may be
    missing from tables created by earlier runs.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS etl_api_products (
                product_id              INTEGER PRIMARY KEY,
                title                   TEXT,
                description             TEXT,
                category                TEXT,
                price                   NUMERIC(10, 2),
                discount_percentage     NUMERIC(5, 2),
                rating                  NUMERIC(3, 2),
                stock                   INTEGER,
                tags                    TEXT,
                brand                   TEXT,
                sku                     TEXT,
                weight                  NUMERIC(8, 2),
                width                   NUMERIC(8, 2),
                height                  NUMERIC(8, 2),
                depth                   NUMERIC(8, 2),
                warranty_information    TEXT,
                shipping_information    TEXT,
                availability_status     TEXT,
                in_stock                BOOLEAN,
                return_policy           TEXT,
                minimum_order_quantity  INTEGER,
                barcode                 TEXT,
                created_at              TEXT,
                updated_at              TEXT,
                thumbnail               TEXT,
                source                  TEXT,
                source_type             TEXT,
                validated_at            TEXT,
                etl_updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # Migration guard: add columns that may be absent from older table versions
        for col_sql in [
            "ALTER TABLE etl_api_products ADD COLUMN IF NOT EXISTS source TEXT",
            "ALTER TABLE etl_api_products ADD COLUMN IF NOT EXISTS source_type TEXT",
            "ALTER TABLE etl_api_products ADD COLUMN IF NOT EXISTS validated_at TEXT",
            "ALTER TABLE etl_api_products ADD COLUMN IF NOT EXISTS in_stock BOOLEAN",
            "ALTER TABLE etl_api_products ADD COLUMN IF NOT EXISTS etl_updated_at TIMESTAMPTZ DEFAULT now()",
        ]:
            cur.execute(col_sql)
    conn.commit()


def ensure_csv_users_table(conn) -> None:
    """Create the etl_csv_users table if it does not already exist.

    Column names mirror the canonical record produced by
    DataTransformer._transform_csv_record().
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS etl_csv_users (
                user_id         TEXT PRIMARY KEY,
                full_name       TEXT,
                email           TEXT,
                phone           TEXT,
                signup_date     DATE,
                country         TEXT,
                age             INTEGER,
                status          TEXT,
                is_active       BOOLEAN,
                newsletter_opt_in BOOLEAN,
                source          TEXT,
                source_type     TEXT,
                validated_at    TEXT,
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # Migration guard: add columns that may be absent from older table versions
        for col_sql in [
            "ALTER TABLE etl_csv_users ADD COLUMN IF NOT EXISTS is_active BOOLEAN",
            "ALTER TABLE etl_csv_users ADD COLUMN IF NOT EXISTS source TEXT",
            "ALTER TABLE etl_csv_users ADD COLUMN IF NOT EXISTS source_type TEXT",
            "ALTER TABLE etl_csv_users ADD COLUMN IF NOT EXISTS validated_at TEXT",
            "ALTER TABLE etl_csv_users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now()",
        ]:
            cur.execute(col_sql)
    conn.commit()


def get_metadata(conn, source_name: str) -> dict | None:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM etl_metadata WHERE source_name = %s", (source_name,)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def update_metadata(
    conn,
    source_name: str,
    last_successful_run: Optional[datetime] = None,
    last_processed_id: Optional[str] = None,
    last_processed_timestamp: Optional[datetime] = None,
) -> None:
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO etl_metadata (source_name, last_successful_run, last_processed_id, last_processed_timestamp, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (source_name) DO UPDATE SET
              last_successful_run = EXCLUDED.last_successful_run,
              last_processed_id = EXCLUDED.last_processed_id,
              last_processed_timestamp = EXCLUDED.last_processed_timestamp,
              updated_at = EXCLUDED.updated_at
            """,
            (source_name, last_successful_run, last_processed_id, last_processed_timestamp, now),
        )
    conn.commit()


def get_existing_ids(conn, table: str, id_column: str) -> Set[str]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {id_column} FROM {table} WHERE {id_column} IS NOT NULL")
        rows = cur.fetchall()
    return {str(r[0]) for r in rows}


def upsert_records(conn, table: str, records: Iterable[dict], id_column: str = "source_id") -> tuple[int, int]:
    """Upsert a sequence of records into `table` using `id_column` as conflict key.

    For the etl_csv_users table (and any table whose records are flat dicts of
    typed column values) each column in the record dict is written to its own
    column.  For legacy callers that pass records with a nested `canonical_record`
    dict the payload is stored as JSONB in a `payload` column (original behaviour).

    Returns: (inserted_count, updated_count)
    """
    inserted = 0
    updated = 0

    records_list = list(records)
    if not records_list:
        return 0, 0

    # Detect which mode to use based on the first record
    sample = records_list[0]
    use_flat_columns = "payload" not in sample and id_column in sample

    with conn.cursor() as cur:
        for rec in records_list:
            if id_column not in rec:
                raise ValueError(f"Record missing id column {id_column!r}: {rec}")

            if use_flat_columns:
                # Build a dynamic flat-column upsert
                columns = list(rec.keys())
                values = [rec[c] for c in columns]
                col_list = ", ".join(f'"{c}"' for c in columns)
                placeholder_list = ", ".join(["%s"] * len(columns))
                update_set = ", ".join(
                    f'"{c}" = EXCLUDED."{c}"'
                    for c in columns
                    if c != id_column
                )
                cur.execute(
                    f"""
                    INSERT INTO {table} ({col_list})
                    VALUES ({placeholder_list})
                    ON CONFLICT ("{id_column}") DO UPDATE SET
                        {update_set}
                    """,
                    values,
                )
            else:
                # Legacy JSONB payload path
                key = rec[id_column]
                payload = json.dumps(rec)
                cur.execute(
                    f"""
                    INSERT INTO {table} ({id_column}, payload, updated_at)
                    VALUES (%s, %s::jsonb, now())
                    ON CONFLICT ({id_column}) DO UPDATE SET
                        payload = EXCLUDED.payload,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (key, payload),
                )

            if cur.rowcount:
                inserted += 1

    conn.commit()
    return inserted, updated
