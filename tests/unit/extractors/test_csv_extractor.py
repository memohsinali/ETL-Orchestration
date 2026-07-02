"""Unit tests for the CSV extractor."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from unittest.mock import MagicMock

from etl.extractors.csv_extractor import CSVExtractor, CSVExtractorConfig, CSVParseError
from etl.extractors.base import ExtractionResult


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_csv_extractor_discovers_csv_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sample_dir = tmp_path / "data"
    sample_dir.mkdir()

    file1 = sample_dir / "sample1.csv"
    file2 = sample_dir / "sample2.csv"
    write_csv(file1, [{"id": "1", "value": "A"}], ["id", "value"])
    write_csv(file2, [{"id": "2", "value": "B"}], ["id", "value"])

    config = CSVExtractorConfig(input_path=str(sample_dir))
    extractor = CSVExtractor(config=config)

    result = extractor.extract()

    assert result.metadata["file_count"] == 2
    assert result.metadata["record_count"] == 2
    assert result.metadata["error_count"] == 0
    assert len(result.records) == 2
    assert result.records[0]["normalized_record"]["id"] == "1"


def test_csv_extractor_preserves_filename_and_metadata(tmp_path: Path):
    sample_dir = tmp_path / "data"
    sample_dir.mkdir()

    sample_file = sample_dir / "example.csv"
    write_csv(sample_file, [{"name": "Alice", "score": "100"}], ["name", "score"])

    config = CSVExtractorConfig(input_path=str(sample_dir))
    extractor = CSVExtractor(config=config)
    result = extractor.extract()

    assert result.records[0]["source_file"] == "example.csv"
    assert "ingestion_timestamp" in result.records[0]
    assert result.records[0]["normalized_record"]["name"] == "Alice"


def test_csv_extractor_skips_malformed_rows(tmp_path: Path):
    sample_dir = tmp_path / "data"
    sample_dir.mkdir()

    malformed_file = sample_dir / "bad.csv"
    malformed_content = "id,value\n1,A\n2"  # second row missing value
    malformed_file.write_text(malformed_content, encoding="utf-8")

    config = CSVExtractorConfig(input_path=str(sample_dir))
    extractor = CSVExtractor(config=config)
    result = extractor.extract()

    assert result.metadata["file_count"] == 1
    assert result.metadata["record_count"] == 1
    assert result.metadata["error_count"] == 1
    assert result.errors[0]["source_file"] == "bad.csv"


def test_csv_extractor_uses_env_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sample_dir = tmp_path / "data"
    sample_dir.mkdir()

    file_path = sample_dir / "env.csv"
    write_csv(file_path, [{"id": "1", "value": "env"}], ["id", "value"])

    monkeypatch.setenv("CSV_INPUT_PATH", str(sample_dir))
    monkeypatch.setenv("CSV_DELIMITER", ",")
    monkeypatch.setenv("CSV_ENCODING", "utf-8")
    monkeypatch.setenv("CSV_HAS_HEADER", "True")

    extractor = CSVExtractor()
    result = extractor.extract()

    assert result.metadata["record_count"] == 1
    assert result.records[0]["normalized_record"]["value"] == "env"


def test_csv_extractor_input_path_missing(tmp_path: Path):
    config = CSVExtractorConfig(input_path=str(tmp_path / "missing"))
    extractor = CSVExtractor(config=config)

    with pytest.raises(FileNotFoundError):
        extractor.extract()


def test_csv_extractor_filters_against_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sample_dir = tmp_path / "data"
    sample_dir.mkdir()

    file_path = sample_dir / "sample.csv"
    write_csv(file_path, [{"id": "1", "value": "A"}, {"id": "2", "value": "B"}, {"id": "3", "value": "C"}], ["id", "value"])

    config = CSVExtractorConfig(input_path=str(sample_dir), source_name="csv_source", source_id_field="id")

    # Mock DB metadata to simulate last_processed_id = 1
    monkeypatch.setattr("etl.extractors.csv_extractor.db_utils._connect", lambda: MagicMock())
    monkeypatch.setattr("etl.extractors.csv_extractor.db_utils.ensure_etl_metadata_table", lambda c: None)
    monkeypatch.setattr("etl.extractors.csv_extractor.db_utils.get_metadata", lambda c, s: {"last_processed_id": "1"})

    extractor = CSVExtractor(config=config)
    result = extractor.extract()

    ids = [r["normalized_record"]["id"] for r in result.records]
    assert ids == ["2", "3"]
