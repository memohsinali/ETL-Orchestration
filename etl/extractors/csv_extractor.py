"""CSV extraction workflow for the ETL orchestrator."""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Optional

from config.settings.config import load_settings
from etl.extractors.base import ExtractionResult
from etl.utils import db as db_utils

logger = logging.getLogger(__name__)


class CSVParseError(Exception):
    """Raised when a CSV parse error should be surfaced."""


@dataclass(frozen=True)
class CSVExtractorConfig:
    input_path: str
    delimiter: str = ","
    encoding: str = "utf-8"
    has_header: bool = True
    # Optional source identifier configuration for incremental extraction
    source_name: Optional[str] = None
    source_id_field: str = "id"


class CSVExtractor:
    """CSV extraction workflow that discovers files and parses rows."""

    def __init__(self, config: CSVExtractorConfig | None = None) -> None:
        self.settings = load_settings()
        self.config = config or self._load_config_from_env()

    def _load_config_from_env(self) -> CSVExtractorConfig:
        """Load CSV extractor configuration from environment variables."""
        return CSVExtractorConfig(
            input_path=os.getenv("CSV_INPUT_PATH", "data"),
            delimiter=os.getenv("CSV_DELIMITER", ","),
            encoding=os.getenv("CSV_ENCODING", "utf-8"),
            has_header=os.getenv("CSV_HAS_HEADER", "True").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            },
            source_name=os.getenv("CSV_SOURCE_NAME") or None,
            source_id_field=os.getenv("CSV_SOURCE_ID_FIELD", "id"),
        )

    def _resolve_source_files(self) -> list[Path]:
        """Discover CSV files under the configured input path."""
        source_dir = Path(self.config.input_path)

        if not source_dir.exists():
            raise FileNotFoundError(f"CSV input path not found: {source_dir}")

        if not source_dir.is_dir():
            raise ValueError(f"CSV input path must be a directory: {source_dir}")

        files = [
            path
            for path in source_dir.rglob("*.csv")
            if path.is_file() and not path.name.startswith(".")
        ]

        logger.info("Discovered %d CSV file(s) in %s", len(files), source_dir)
        return sorted(files)

    def _normalize_row(
        self,
        row: dict[str, str],
        source_file: Path,
        row_number: int,
    ) -> dict[str, Any]:
        """Normalize a CSV row into a record with metadata."""
        return {
            "source_file": source_file.name,
            "ingestion_timestamp": datetime.utcnow().isoformat() + "Z",
            "row_number": row_number,
            "raw_record": row,
            "normalized_record": {
                k.strip(): v.strip()
                for k, v in row.items()
            },
        }

    def extract(self) -> ExtractionResult:
        """Extract records from discovered CSV files."""
        records: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        processed_files = 0

        for csv_file in self._resolve_source_files():

            try:
                with csv_file.open(
                    mode="r",
                    encoding=self.config.encoding,
                    newline="",
                ) as stream:

                    processed_files += 1

                    reader = (
                        csv.DictReader(
                            stream,
                            delimiter=self.config.delimiter,
                        )
                        if self.config.has_header
                        else csv.DictReader(
                            stream,
                            delimiter=self.config.delimiter,
                            fieldnames=None,
                        )
                    )

                    for row_number, row in enumerate(reader, start=1):

                        try:
                            if row is None:
                                raise CSVParseError("Empty row encountered.")

                            # Skip completely empty rows.
                            if not any(
                                (value or "").strip()
                                for value in row.values()
                            ):
                                logger.info(
                                    "Skipping empty row %d in %s",
                                    row_number,
                                    csv_file.name,
                                )
                                continue

                            # Detect malformed rows with missing columns.
                            if any(value is None for value in row.values()):
                                raise CSVParseError(
                                    "Row has missing column value(s)."
                                )

                            normalized = self._normalize_row(
                                row=row,
                                source_file=csv_file,
                                row_number=row_number,
                            )

                            records.append(normalized)

                        except CSVParseError as exc:
                            error_detail = {
                                "source_file": csv_file.name,
                                "row_number": row_number,
                                "error": str(exc),
                                "row": row,
                            }

                            logger.warning(
                                "Skipping malformed row in %s (row %d): %s",
                                csv_file.name,
                                row_number,
                                exc,
                            )

                            errors.append(error_detail)

            except Exception as exc:
                error_detail = {
                    "source_file": csv_file.name,
                    "row_number": None,
                    "error": f"Failed to process file: {exc}",
                    "row": None,
                }

                logger.error(
                    "Skipping file %s because it could not be processed: %s",
                    csv_file.name,
                    exc,
                )

                errors.append(error_detail)

        metadata = {
            "source_path": self.config.input_path,
            "file_count": processed_files,
            "record_count": len(records),
            "error_count": len(errors),
        }

        # If a source_name is provided, consult etl_metadata and filter out
        # already-processed records based on the configured id field.
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
                        # normalized_record contains the CSV columns
                        val = None
                        try:
                            val = rec.get("normalized_record", {}).get(sid)
                        except Exception:
                            val = None

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

        metadata["record_count"] = len(new_records)

        logger.info(
            "CSV extraction summary total_found=%d new=%d skipped=%d",
            len(records),
            len(new_records),
            skipped,
        )

        return ExtractionResult(
            records=new_records,
            metadata=metadata,
            errors=errors,
        )