"""MongoDB extraction workflow for the ETL orchestrator."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from config.settings.config import load_settings
from etl.extractors.base import ExtractionResult
from etl.utils import db as db_utils

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MongoExtractorConfig:
    collection: str
    query: dict[str, Any] = None
    limit: Optional[int] = None
    # Optional source identifier configuration for incremental extraction
    source_name: Optional[str] = None
    source_id_field: str = "_id"


class MongoExtractor:
    """MongoDB extractor that queries a collection and normalizes documents.

    The implementation keeps extraction lightweight; callers may mock pymongo
    in tests. Incremental filtering consults `etl_metadata` when
    `source_name` is provided.
    """

    def __init__(self, config: MongoExtractorConfig | None = None) -> None:
        self.settings = load_settings()
        self.config = config or MongoExtractorConfig(collection="default", query={})

    def extract(self) -> ExtractionResult:
        records: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        # Import pymongo lazily so tests can monkeypatch the module.
        try:
            import pymongo  # type: ignore
        except Exception as exc:  # pragma: no cover - environment may not have pymongo
            logger.error("pymongo not available: %s", exc)
            errors.append({"error": "pymongo not available", "detail": str(exc)})
            return ExtractionResult(records=records, metadata={"record_count": 0}, errors=errors)

        client = None
        try:
            client = pymongo.MongoClient(self.settings.mongo_uri)
            db = client[self.settings.mongo_db]
            coll = db[self.config.collection]

            cursor = coll.find(self.config.query or {})
            if self.config.limit:
                cursor = cursor.limit(self.config.limit)

            for doc in cursor:
                # Normalize document - ensure _id is string for transport.
                normalized = {
                    "source_collection": self.config.collection,
                    "ingestion_timestamp": datetime.now(timezone.utc).isoformat(),
                    "raw_record": doc,
                    "normalized_record": {**{k: (str(v) if k == "_id" else v) for k, v in doc.items()}},
                }
                records.append(normalized)

        except Exception as exc:
            logger.error("Failed to extract from MongoDB: %s", exc)
            errors.append({"error": str(exc)})

        finally:
            if client:
                try:
                    client.close()
                except Exception:
                    pass

        # Incremental filtering against etl_metadata if source_name configured
        skipped = 0
        new_records = records
        if self.config.source_name:
            conn = None
            try:
                conn = db_utils._connect()
                db_utils.ensure_etl_metadata_table(conn)
                meta = db_utils.get_metadata(conn, self.config.source_name)
                if meta and meta.get("last_processed_id") is not None:
                    last_id = meta.get("last_processed_id")
                    try:
                        last_id_val = int(last_id)
                    except Exception:
                        last_id_val = last_id

                    sid = self.config.source_id_field
                    filtered = []
                    for rec in records:
                        val = rec.get("normalized_record", {}).get(sid)
                        try:
                            use_val = int(val) if val is not None else None
                        except Exception:
                            use_val = val

                        if last_id_val is None or use_val is None:
                            filtered.append(rec)
                        else:
                            try:
                                if use_val > last_id_val:
                                    filtered.append(rec)
                                else:
                                    skipped += 1
                            except Exception:
                                filtered.append(rec)

                    new_records = filtered
            finally:
                if conn:
                    conn.close()

        metadata = {
            "source_collection": self.config.collection,
            "record_count": len(new_records),
            "error_count": len(errors),
        }

        logger.info("Mongo extraction summary total_found=%d new=%d skipped=%d", len(records), len(new_records), skipped)

        return ExtractionResult(records=new_records, metadata=metadata, errors=errors)
