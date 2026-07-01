"""PostgreSQL loader with UPSERT semantics and metadata checkpointing.

This loader upserts incoming records using a configurable conflict key and
updates the `etl_metadata` table only after a successful load.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from etl.utils import db


class PostgresLoader:
    def __init__(
        self,
        table: str,
        id_column: str = "source_id",
        source_name: Optional[str] = None,
        auto_create_table: bool = False,
    ):
        self.table = table
        self.id_column = id_column
        self.source_name = source_name or table
        self.auto_create_table = auto_create_table

    def _normalize_record(self, record: Any) -> dict[str, Any]:
        """Flatten a CanonicalRecord or plain dict into a loader-ready dict.

        For tables that use flat columns (etl_csv_users, etl_api_products,
        etl_mongo_users) the canonical_record dict already contains all the
        columns including 'source' and 'source_type'.  We must NOT inject
        extra keys like 'source_name' that don't exist in those tables.

        We only need to ensure the id_column is present so the upsert can
        find the primary key.
        """
        if isinstance(record, dict):
            if "canonical_record" in record and isinstance(record["canonical_record"], dict):
                flat = dict(record["canonical_record"])
                # Ensure the id_column is present — fall back to record_id
                # from the wrapper dict if the canonical record doesn't have it.
                if self.id_column not in flat:
                    flat[self.id_column] = record.get("record_id")
                return flat
            # Plain dict — drop wrapper-level keys that don't belong in the table
            plain = {k: v for k, v in record.items()
                     if k not in ("source_name", "record_id", "schema_version")}
            return plain

        if hasattr(record, "canonical_record"):
            payload = dict(getattr(record, "canonical_record"))
            if self.id_column not in payload:
                payload[self.id_column] = getattr(record, "record_id", None)
            return payload

        raise TypeError(f"Unsupported record type for loader: {type(record)!r}")

    def load(self, records: Iterable[Any]) -> dict:
        conn = db._connect()
        try:
            db.ensure_etl_metadata_table(conn)

            # Auto-create the target table if requested
            if self.auto_create_table:
                if self.table == "etl_csv_users":
                    db.ensure_csv_users_table(conn)
                elif self.table == "etl_api_products":
                    db.ensure_api_products_table(conn)
                elif self.table == "etl_mongo_users":
                    db.ensure_mongo_users_table(conn)

            records_list = [self._normalize_record(record) for record in records]

            # Filter out already-loaded records to avoid redundant work
            try:
                existing = db.get_existing_ids(conn, self.table, self.id_column)
            except Exception:
                existing = set()

            new_records = [
                r for r in records_list
                if str(r.get(self.id_column)) not in existing
            ]
            skipped = len(records_list) - len(new_records)

            inserted, updated = db.upsert_records(
                conn, self.table, new_records, id_column=self.id_column
            )

            # Checkpoint — persist last processed id after successful upsert
            now = datetime.now(timezone.utc)
            last_id: Optional[str] = None

            all_ids = [
                r.get(self.id_column)
                for r in records_list
                if r.get(self.id_column) is not None
            ]
            if all_ids:
                numeric_ids = []
                non_numeric = False
                for v in all_ids:
                    try:
                        numeric_ids.append(int(v))
                    except Exception:
                        non_numeric = True
                if numeric_ids and not non_numeric:
                    last_id = str(max(numeric_ids))
                else:
                    last_id = max(str(v) for v in all_ids)

            db.update_metadata(
                conn,
                self.source_name,
                last_successful_run=now,
                last_processed_id=last_id,
                last_processed_timestamp=now,
            )

            return {
                "inserted": inserted,
                "updated": updated,
                "skipped": skipped,
            }
        finally:
            conn.close()
