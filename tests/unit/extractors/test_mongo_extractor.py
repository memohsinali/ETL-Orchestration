"""Unit tests for the MongoDB extractor."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from etl.extractors.mongo_extractor import MongoExtractor, MongoExtractorConfig


def test_mongo_extractor_filters_against_metadata(monkeypatch: pytest.MonkeyPatch):
    # Prepare fake pymongo with a client that returns a collection iterator
    client = MagicMock()
    db = MagicMock()
    coll = MagicMock()

    docs = [{"_id": 1, "value": "A"}, {"_id": 2, "value": "B"}, {"_id": 3, "value": "C"}]

    coll.find.return_value = docs
    db.__getitem__.return_value = coll
    client.__getitem__.return_value = db

    fake_pymongo = SimpleNamespace(MongoClient=lambda uri: client)
    monkeypatch.setitem(sys.modules, "pymongo", fake_pymongo)

    # Patch DB metadata functions to simulate last_processed_id = 1
    monkeypatch.setattr("etl.extractors.mongo_extractor.db_utils._connect", lambda: MagicMock())
    monkeypatch.setattr("etl.extractors.mongo_extractor.db_utils.ensure_etl_metadata_table", lambda c: None)
    monkeypatch.setattr("etl.extractors.mongo_extractor.db_utils.get_metadata", lambda c, s: {"last_processed_id": "1"})

    config = MongoExtractorConfig(collection="testcoll", source_name="mongo_source", source_id_field="_id")
    extractor = MongoExtractor(config=config)
    result = extractor.extract()

    ids = [r["normalized_record"]["_id"] for r in result.records]
    assert ids == ["2", "3"]
