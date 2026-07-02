"""Unit tests for the Postgres loader incremental behaviour and metadata updates."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from etl.loaders.postgres_loader import PostgresLoader
from etl.utils import db as db_module


def make_conn():
    conn = MagicMock()
    conn.close = MagicMock()
    return conn


def test_load_inserts_and_updates_metadata(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(db_module, "_connect", lambda: conn)
    monkeypatch.setattr(db_module, "ensure_etl_metadata_table", lambda c: None)
    monkeypatch.setattr(db_module, "get_existing_ids", lambda c, t, idc: set())
    monkeypatch.setattr(db_module, "upsert_records", lambda c, t, recs, id_column=None: (len(list(recs)), 0))
    called = {}

    def fake_update_metadata(c, source_name, last_successful_run=None, last_processed_id=None, last_processed_timestamp=None):
        called["updated"] = True
        called["last_processed_id"] = last_processed_id

    monkeypatch.setattr(db_module, "update_metadata", fake_update_metadata)

    loader = PostgresLoader(table="test_table", id_column="id", source_name="test_source")
    records = [{"id": "1", "value": "a"}, {"id": "2", "value": "b"}]

    result = loader.load(records)

    assert result["inserted"] == 2
    assert called.get("updated") is True
    assert called.get("last_processed_id") == "2"


def test_load_skips_existing_records(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(db_module, "_connect", lambda: conn)
    monkeypatch.setattr(db_module, "ensure_etl_metadata_table", lambda c: None)
    monkeypatch.setattr(db_module, "get_existing_ids", lambda c, t, idc: {"1"})

    def fake_upsert(c, t, recs, id_column=None):
        recs = list(recs)
        assert len(recs) == 1
        return (1, 0)

    monkeypatch.setattr(db_module, "upsert_records", fake_upsert)
    last_called = {}

    def fake_update_metadata(c, source_name, last_successful_run=None, last_processed_id=None, last_processed_timestamp=None):
        last_called["last_processed_id"] = last_processed_id

    monkeypatch.setattr(db_module, "update_metadata", fake_update_metadata)

    loader = PostgresLoader(table="test_table", id_column="id", source_name="test_source")
    records = [{"id": "1", "value": "a"}, {"id": "2", "value": "b"}]

    result = loader.load(records)

    assert result["inserted"] == 1
    assert last_called.get("last_processed_id") == "2"


def test_load_accepts_canonical_records(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(db_module, "_connect", lambda: conn)
    monkeypatch.setattr(db_module, "ensure_etl_metadata_table", lambda c: None)
    monkeypatch.setattr(db_module, "get_existing_ids", lambda c, t, idc: set())

    captured = {}

    def fake_upsert(c, t, recs, id_column=None):
        captured["records"] = list(recs)
        return (1, 0)

    monkeypatch.setattr(db_module, "upsert_records", fake_upsert)

    loader = PostgresLoader(table="users", id_column="id", source_name="canonical_source")
    record = SimpleNamespace(
        record_id="99",
        source_name="api-source",
        source_type="api",
        canonical_record={"id": "99", "email": "ada@example.com"},
    )

    loader.load([record])

    assert captured["records"][0]["id"] == "99"
    assert captured["records"][0]["source_name"] == "api-source"
