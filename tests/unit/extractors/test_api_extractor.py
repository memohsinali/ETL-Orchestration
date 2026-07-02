"""Unit tests for the REST API Extractor (SPEC-005).

Run with:
    python -m pytest -v tests/unit/extractors/test_api_extractor.py
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from etl.extractors.api_extractor import (
    APIExtractor,
    APIExtractorConfig,
    APIRequestError,
    APIResponseError,
    AuthenticationError,
)


def make_response(status_code=200, json_data=None, headers=None, url="https://api.test/items"):
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.url = url
    response.headers = headers or {}
    response.json.return_value = json_data if json_data is not None else {}
    response.text = str(json_data)
    return response


# --------------------------------------------------------------------------- #
# Success path
# --------------------------------------------------------------------------- #

class TestSuccessfulExtraction:
    def test_single_page_success(self):
        session = MagicMock()
        session.request.return_value = make_response(
            json_data=[{"id": 1}, {"id": 2}]
        )
        config = APIExtractorConfig(base_url="https://api.test", endpoint="items")

        result = APIExtractor(config, session=session).extract()

        assert result.records == [{"id": 1}, {"id": 2}]
        assert result.errors == []
        assert result.metadata["record_count"] == 2
        session.request.assert_called_once()

    def test_records_path_extraction(self):
        session = MagicMock()
        session.request.return_value = make_response(
            json_data={"data": {"items": [{"id": 1}]}}
        )
        config = APIExtractorConfig(
            base_url="https://api.test", endpoint="items", records_path="data.items"
        )

        result = APIExtractor(config, session=session).extract()

        assert result.records == [{"id": 1}]

    def test_single_object_payload_is_wrapped_in_list(self):
        session = MagicMock()
        session.request.return_value = make_response(json_data={"id": 1, "name": "solo"})
        config = APIExtractorConfig(base_url="https://api.test", endpoint="item")

        result = APIExtractor(config, session=session).extract()

        assert result.records == [{"id": 1, "name": "solo"}]


# --------------------------------------------------------------------------- #
# Failure path
# --------------------------------------------------------------------------- #

class TestFailureHandling:
    def test_non_success_status_recorded_as_error_not_raised(self):
        session = MagicMock()
        session.request.return_value = make_response(status_code=404, json_data={})
        config = APIExtractorConfig(base_url="https://api.test", endpoint="missing", max_retries=0)

        result = APIExtractor(config, session=session).extract()

        assert result.records == []
        assert len(result.errors) == 1
        assert result.errors[0]["status_code"] == 404

    def test_invalid_json_payload_is_reported_as_error(self):
        session = MagicMock()
        response = make_response(status_code=200)
        response.json.side_effect = ValueError("not json")
        session.request.return_value = response
        config = APIExtractorConfig(base_url="https://api.test", endpoint="items", max_retries=0)

        result = APIExtractor(config, session=session).extract()

        assert result.records == []
        assert len(result.errors) == 1

    def test_network_error_retries_then_fails_gracefully(self):
        session = MagicMock()
        session.request.side_effect = requests.exceptions.ConnectionError("boom")
        config = APIExtractorConfig(
            base_url="https://api.test", endpoint="items", max_retries=2, retry_backoff_seconds=0
        )

        with patch("etl.extractors.api_extractor.time.sleep"):
            result = APIExtractor(config, session=session).extract()

        assert result.records == []
        assert len(result.errors) == 1
        assert session.request.call_count == 3  # initial + 2 retries

    def test_retryable_status_eventually_succeeds(self):
        session = MagicMock()
        session.request.side_effect = [
            make_response(status_code=503),
            make_response(status_code=200, json_data=[{"id": 1}]),
        ]
        config = APIExtractorConfig(
            base_url="https://api.test", endpoint="items", max_retries=2, retry_backoff_seconds=0
        )

        with patch("etl.extractors.api_extractor.time.sleep"):
            result = APIExtractor(config, session=session).extract()

        assert result.records == [{"id": 1}]
        assert result.errors == []


# --------------------------------------------------------------------------- #
# Pagination - classic page-number style
# --------------------------------------------------------------------------- #

class TestPagePagination:
    def test_walks_pages_until_short_page_returned(self):
        session = MagicMock()
        session.request.side_effect = [
            make_response(json_data=[{"id": 1}, {"id": 2}]),  # full page
            make_response(json_data=[{"id": 3}, {"id": 4}]),  # full page
            make_response(json_data=[{"id": 5}]),              # short page -> stop
        ]
        config = APIExtractorConfig(
            base_url="https://api.test", endpoint="items", page_size=2
        )

        result = APIExtractor(config, session=session).extract()

        assert [r["id"] for r in result.records] == [1, 2, 3, 4, 5]
        assert session.request.call_count == 3
        assert result.metadata["pages_fetched"] == 3

    def test_page_param_increments_by_one_starting_at_start_page(self):
        session = MagicMock()
        session.request.side_effect = [
            make_response(json_data=[{"id": 1}, {"id": 2}]),
            make_response(json_data=[{"id": 3}]),
        ]
        config = APIExtractorConfig(
            base_url="https://api.test", endpoint="items",
            page_size=2, page_param="page", start_page=1,
        )

        APIExtractor(config, session=session).extract()

        first_call_params = session.request.call_args_list[0].kwargs["params"]
        second_call_params = session.request.call_args_list[1].kwargs["params"]
        assert first_call_params["page"] == 1
        assert second_call_params["page"] == 2

    def test_empty_page_stops_pagination(self):
        session = MagicMock()
        session.request.return_value = make_response(json_data=[])
        config = APIExtractorConfig(base_url="https://api.test", endpoint="items", page_size=10)

        result = APIExtractor(config, session=session).extract()

        assert result.records == []
        session.request.assert_called_once()

    def test_max_pages_caps_iteration(self):
        session = MagicMock()
        session.request.return_value = make_response(json_data=[{"id": 1}, {"id": 2}])
        config = APIExtractorConfig(
            base_url="https://api.test", endpoint="items", page_size=2, max_pages=2
        )

        result = APIExtractor(config, session=session).extract()

        assert session.request.call_count == 2
        assert result.metadata["pages_fetched"] == 2

    def test_pagination_disabled_makes_single_request(self):
        session = MagicMock()
        session.request.return_value = make_response(json_data=[{"id": 1}])
        config = APIExtractorConfig(base_url="https://api.test", endpoint="items")  # no page_size

        APIExtractor(config, session=session).extract()

        session.request.assert_called_once()


# --------------------------------------------------------------------------- #
# Pagination - offset/skip style (dummyjson.com and similar APIs)
# --------------------------------------------------------------------------- #

class TestOffsetPagination:
    def test_skip_param_increments_by_page_size_not_by_one(self):
        """Regression test: skip must advance by page_size each call
        (0, 10, 20 ...), not by 1 (0, 1, 2 ...)."""
        session = MagicMock()
        session.request.side_effect = [
            make_response(json_data={"products": [{"id": i} for i in range(1, 11)], "total": 25}),
            make_response(json_data={"products": [{"id": i} for i in range(11, 21)], "total": 25}),
            make_response(json_data={"products": [{"id": i} for i in range(21, 26)], "total": 25}),
        ]
        config = APIExtractorConfig(
            base_url="https://dummyjson.com",
            endpoint="products",
            pagination_style="offset",
            page_param="skip",
            page_size_param="limit",
            page_size=10,
            start_page=0,
            records_path="products",
            total_path="total",
        )

        result = APIExtractor(config, session=session).extract()

        skips = [call.kwargs["params"]["skip"] for call in session.request.call_args_list]
        assert skips == [0, 10, 20]
        assert len(result.records) == 25
        assert result.metadata["pages_fetched"] == 3

    def test_stops_when_total_reached(self):
        session = MagicMock()
        session.request.side_effect = [
            make_response(json_data={"products": [{"id": i} for i in range(1, 11)], "total": 10}),
        ]
        config = APIExtractorConfig(
            base_url="https://dummyjson.com",
            endpoint="products",
            pagination_style="offset",
            page_param="skip",
            page_size_param="limit",
            page_size=10,
            start_page=0,
            records_path="products",
            total_path="total",
        )

        result = APIExtractor(config, session=session).extract()

        session.request.assert_called_once()
        assert len(result.records) == 10

    def test_stops_on_short_page_when_total_not_provided(self):
        session = MagicMock()
        session.request.side_effect = [
            make_response(json_data={"products": [{"id": i} for i in range(1, 11)]}),
            make_response(json_data={"products": [{"id": i} for i in range(11, 16)]}),  # short page
        ]
        config = APIExtractorConfig(
            base_url="https://dummyjson.com",
            endpoint="products",
            pagination_style="offset",
            page_param="skip",
            page_size_param="limit",
            page_size=10,
            start_page=0,
            records_path="products",
        )

        result = APIExtractor(config, session=session).extract()

        assert session.request.call_count == 2
        assert len(result.records) == 15

    def test_invalid_pagination_style_raises_at_construction(self):
        with pytest.raises(ValueError):
            APIExtractorConfig(
                base_url="https://dummyjson.com",
                endpoint="products",
                pagination_style="bogus",
            )


# --------------------------------------------------------------------------- #
# Config loaded from environment / .env (mirrors CSVExtractorConfig pattern)
# --------------------------------------------------------------------------- #

class TestConfigFromEnv:
    def test_from_env_reads_base_url_and_endpoint(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("API_BASE_URL", "https://dummyjson.com")
        monkeypatch.setenv("API_ENDPOINT", "products")
        monkeypatch.setenv("API_PAGE_SIZE", "10")
        monkeypatch.setenv("API_PAGE_PARAM", "skip")
        monkeypatch.setenv("API_PAGE_SIZE_PARAM", "limit")
        monkeypatch.setenv("API_PAGINATION_STYLE", "offset")
        monkeypatch.setenv("API_START_PAGE", "0")
        monkeypatch.setenv("API_RECORDS_PATH", "products")
        monkeypatch.setenv("API_TOTAL_PATH", "total")
        monkeypatch.delenv("API_AUTH_TOKEN", raising=False)

        config = APIExtractorConfig.from_env()

        assert config.base_url == "https://dummyjson.com"
        assert config.endpoint == "products"
        assert config.request_url() == "https://dummyjson.com/products"
        assert config.page_size == 10
        assert config.pagination_style == "offset"
        assert config.page_param == "skip"
        assert config.start_page == 0
        assert config.records_path == "products"
        assert config.total_path == "total"
        assert config.auth_type == "none"
        assert config.auth_token is None

    def test_from_env_defaults_to_bearer_when_token_present(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("API_BASE_URL", "https://api.test")
        monkeypatch.setenv("API_AUTH_TOKEN", "secret-token")
        monkeypatch.delenv("API_AUTH_TYPE", raising=False)

        config = APIExtractorConfig.from_env()

        assert config.auth_type == "bearer"
        assert config.auth_token == "secret-token"


def test_api_extractor_filters_against_metadata(monkeypatch):
    # Setup a two-page response with ids 1..3 and metadata indicating last_processed_id=1
    session = MagicMock()
    session.request.side_effect = [
        make_response(json_data={"products": [{"id": 1}, {"id": 2}], "total": 3}),
        make_response(json_data={"products": [{"id": 3}], "total": 3}),
    ]

    config = APIExtractorConfig(
        base_url="https://api.test",
        endpoint="products",
        pagination_style="offset",
        page_param="skip",
        page_size_param="limit",
        page_size=2,
        start_page=0,
        records_path="products",
        total_path="total",
        source_name="products_source",
        source_id_field="id",
    )

    # Mock DB metadata to simulate last_processed_id = 1
    monkeypatch.setattr("etl.extractors.api_extractor.db_utils._connect", lambda: MagicMock())
    fake_conn = MagicMock()
    monkeypatch.setattr("etl.extractors.api_extractor.db_utils._connect", lambda: fake_conn)
    monkeypatch.setattr("etl.extractors.api_extractor.db_utils.ensure_etl_metadata_table", lambda c: None)
    monkeypatch.setattr("etl.extractors.api_extractor.db_utils.get_metadata", lambda c, s: {"last_processed_id": "1"})

    extractor = APIExtractor(config=config, session=session)
    result = extractor.extract()

    # Should filter out id 1 and only return 2 and 3
    ids = [r["id"] for r in result.records]
    assert ids == [2, 3]

    def test_api_extractor_with_no_args_uses_env_config(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("API_BASE_URL", "https://dummyjson.com")
        monkeypatch.setenv("API_ENDPOINT", "products")
        monkeypatch.setenv("API_PAGE_SIZE", "10")
        monkeypatch.setenv("API_PAGE_PARAM", "skip")
        monkeypatch.setenv("API_PAGE_SIZE_PARAM", "limit")
        monkeypatch.setenv("API_PAGINATION_STYLE", "offset")
        monkeypatch.setenv("API_START_PAGE", "0")
        monkeypatch.setenv("API_RECORDS_PATH", "products")
        monkeypatch.setenv("API_TOTAL_PATH", "total")
        monkeypatch.delenv("API_AUTH_TOKEN", raising=False)

        session = MagicMock()
        session.request.return_value = make_response(
            json_data={"products": [{"id": 1}], "total": 1}
        )

        result = APIExtractor(session=session).extract()

        assert result.records == [{"id": 1}]
        called_url = session.request.call_args.kwargs["url"]
        assert called_url == "https://dummyjson.com/products"


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #

class TestAuthentication:
    def test_bearer_token_added_to_headers(self):
        session = MagicMock()
        session.request.return_value = make_response(json_data=[])
        config = APIExtractorConfig(
            base_url="https://api.test", endpoint="items",
            auth_type="bearer", auth_token="secret-token",
        )

        APIExtractor(config, session=session).extract()

        _, kwargs = session.request.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer secret-token"

    def test_api_key_header_added(self):
        session = MagicMock()
        session.request.return_value = make_response(json_data=[])
        config = APIExtractorConfig(
            base_url="https://api.test", endpoint="items",
            auth_type="api_key", auth_token="key-123", auth_header_name="X-API-Key",
        )

        APIExtractor(config, session=session).extract()

        _, kwargs = session.request.call_args
        assert kwargs["headers"]["X-API-Key"] == "key-123"

    def test_basic_auth_passed_through(self):
        session = MagicMock()
        session.request.return_value = make_response(json_data=[])
        config = APIExtractorConfig(
            base_url="https://api.test", endpoint="items",
            auth_type="basic", basic_auth_user="u", basic_auth_password="p",
        )

        APIExtractor(config, session=session).extract()

        _, kwargs = session.request.call_args
        assert kwargs["auth"] == ("u", "p")

    def test_missing_bearer_token_raises_before_request(self):
        session = MagicMock()
        config = APIExtractorConfig(
            base_url="https://api.test", endpoint="items", auth_type="bearer"
        )

        with pytest.raises(AuthenticationError):
            APIExtractor(config, session=session).extract()

        session.request.assert_not_called()

    def test_401_response_raises_authentication_error_immediately(self):
        session = MagicMock()
        session.request.return_value = make_response(status_code=401)
        config = APIExtractorConfig(
            base_url="https://api.test", endpoint="items",
            auth_type="bearer", auth_token="bad-token", max_retries=3,
        )

        with pytest.raises(AuthenticationError):
            APIExtractor(config, session=session).extract()

        session.request.assert_called_once()  # must not retry auth failures

    def test_auth_token_not_present_in_extraction_metadata(self):
        session = MagicMock()
        session.request.return_value = make_response(json_data=[{"id": 1}])
        config = APIExtractorConfig(
            base_url="https://api.test", endpoint="items",
            auth_type="bearer", auth_token="super-secret",
        )

        result = APIExtractor(config, session=session).extract()

        assert "super-secret" not in str(result.metadata)