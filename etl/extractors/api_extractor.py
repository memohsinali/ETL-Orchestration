"""REST API Extractor.

Implements SPEC-005: retrieves data from a configured REST API, handles
authentication and pagination, and normalizes the response payload into the
shared ``ExtractionResult`` shape used by every extractor in this project.

Public entry point: ``APIExtractor.extract()``.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from etl.extractors.base import Extractor, ExtractionResult
from etl.utils.logging import get_logger
from etl.utils import db as db_utils

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Exceptions (per SPEC-005 Section 10)
# --------------------------------------------------------------------------- #

class APIExtractorError(Exception):
    """Base class for all API extractor errors."""


class AuthenticationError(APIExtractorError):
    """Raised when the API rejects the configured credentials (401/403)."""


class APIRequestError(APIExtractorError):
    """Raised when a request cannot be completed (network/timeout/retries exhausted)."""


class APIResponseError(APIExtractorError):
    """Raised for non-success HTTP responses or payloads that cannot be parsed."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

@dataclass
class APIExtractorConfig:
    """Everything the extractor needs to talk to one API endpoint.

    Maps to SPEC-005 Section 13 environment variables, but is deliberately a
    plain dataclass rather than a direct ConfigurationProvider lookup, so the
    extractor stays decoupled and easy to unit test. Build this from your
    SPEC-002 ConfigurationProvider at the call site, e.g.:

        config = APIExtractorConfig(
            base_url=settings.get("API_BASE_URL"),
            auth_token=settings.get("API_AUTH_TOKEN"),
            timeout=float(settings.get("API_TIMEOUT", 30)),
            page_size=int(settings.get("API_PAGE_SIZE", 50)),
        )

    Pagination styles (FR-004):
        "page"   - page_param is a 1-based (or start_page-based) page number
                   that increments by 1 each request, e.g. ?page=1, ?page=2 ...
                   This is the classic "page-number" pagination style.
        "offset" - page_param is a running offset/skip value that increments
                   by page_size each request, e.g. ?skip=0, ?skip=10, ?skip=20 ...
                   This is what dummyjson.com and many "skip/limit" APIs use.
    """

    base_url: str
    endpoint: str = ""
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    query_params: dict[str, Any] = field(default_factory=dict)
    json_body: Optional[dict[str, Any]] = None
    timeout: float = 30.0

    # Authentication. auth_type is one of: "none", "bearer", "api_key", "basic"
    auth_type: str = "none"
    auth_token: Optional[str] = None          # bearer token / api key value
    auth_header_name: str = "Authorization"   # header used for bearer/api_key
    basic_auth_user: Optional[str] = None
    basic_auth_password: Optional[str] = None

    # Pagination (per FR-004). Set page_size=None to disable.
    pagination_style: str = "page"  # "page" or "offset" - see class docstring
    page_param: str = "page"
    page_size_param: str = "page_size"
    page_size: Optional[int] = None
    start_page: int = 1
    max_pages: Optional[int] = None
    records_path: Optional[str] = None  # dotted path to the list, e.g. "data.items"
    total_path: Optional[str] = None    # dotted path to a total-count field, e.g. "total"
    # Optional source identifier configuration for incremental extraction
    source_name: Optional[str] = None
    source_id_field: str = "id"

    # Retries for transient failures (NFR: Reliability)
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0
    retry_status_codes: tuple[int, ...] = (429, 500, 502, 503, 504)

    def __post_init__(self) -> None:
        if self.pagination_style not in ("page", "offset"):
            raise ValueError(
                f"pagination_style must be 'page' or 'offset', got {self.pagination_style!r}"
            )

    def request_url(self) -> str:
        if not self.endpoint:
            return self.base_url
        return f"{self.base_url.rstrip('/')}/{self.endpoint.lstrip('/')}"

    @classmethod
    def from_env(cls) -> "APIExtractorConfig":
        """Build a config from environment variables (SPEC-005 Section 13).

        Mirrors ``CSVExtractorConfig._load_config_from_env`` so both
        extractors can be constructed the same way: ``APIExtractor()`` with
        no arguments, pulling everything from a loaded ``.env`` file.
        """

        def _int_or_none(value: Optional[str]) -> Optional[int]:
            if value is None or value.strip() == "":
                return None
            return int(value)

        def _bool(value: str, default: bool = False) -> bool:
            if value is None:
                return default
            return value.strip().lower() in {"1", "true", "yes", "on"}

        auth_token = os.getenv("API_AUTH_TOKEN") or None

        return cls(
            base_url=os.getenv("API_BASE_URL", ""),
            endpoint=os.getenv("API_ENDPOINT", ""),
            method=os.getenv("API_METHOD", "GET"),
            timeout=float(os.getenv("API_TIMEOUT", "30")),
            auth_type=os.getenv("API_AUTH_TYPE", "bearer" if auth_token else "none"),
            auth_token=auth_token,
            auth_header_name=os.getenv("API_AUTH_HEADER_NAME", "Authorization"),
            basic_auth_user=os.getenv("API_BASIC_AUTH_USER") or None,
            basic_auth_password=os.getenv("API_BASIC_AUTH_PASSWORD") or None,
            pagination_style=os.getenv("API_PAGINATION_STYLE", "page"),
            page_param=os.getenv("API_PAGE_PARAM", "page"),
            page_size_param=os.getenv("API_PAGE_SIZE_PARAM", "page_size"),
            page_size=_int_or_none(os.getenv("API_PAGE_SIZE")),
            start_page=int(os.getenv("API_START_PAGE", "1")),
            max_pages=_int_or_none(os.getenv("API_MAX_PAGES")),
            records_path=os.getenv("API_RECORDS_PATH") or None,
            total_path=os.getenv("API_TOTAL_PATH") or None,
            max_retries=int(os.getenv("API_MAX_RETRIES", "3")),
            retry_backoff_seconds=float(os.getenv("API_RETRY_BACKOFF_SECONDS", "1.0")),
            source_name=os.getenv("API_SOURCE_NAME") or None,
            source_id_field=os.getenv("API_SOURCE_ID_FIELD", "id"),
        )


# --------------------------------------------------------------------------- #
# Request Builder
# --------------------------------------------------------------------------- #

class RequestBuilder:
    """Prepares request kwargs for ``requests`` from configuration (FR-002, FR-005)."""

    def __init__(self, config: APIExtractorConfig):
        self._config = config

    def build(self, page_params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        params = dict(self._config.query_params)
        if page_params:
            params.update(page_params)

        kwargs: dict[str, Any] = {
            "method": self._config.method,
            "url": self._config.request_url(),
            "headers": self._build_headers(),
            "params": params,
            "timeout": self._config.timeout,
        }
        if self._config.json_body is not None:
            kwargs["json"] = self._config.json_body

        auth = self._build_basic_auth()
        if auth is not None:
            kwargs["auth"] = auth

        return kwargs

    def _build_headers(self) -> dict[str, str]:
        headers = dict(self._config.headers)
        if self._config.auth_type == "bearer":
            if not self._config.auth_token:
                raise AuthenticationError("Bearer auth selected but no auth_token configured.")
            headers[self._config.auth_header_name] = f"Bearer {self._config.auth_token}"
        elif self._config.auth_type == "api_key":
            if not self._config.auth_token:
                raise AuthenticationError("API key auth selected but no auth_token configured.")
            headers[self._config.auth_header_name] = self._config.auth_token
        return headers

    def _build_basic_auth(self) -> Optional[tuple[str, str]]:
        if self._config.auth_type != "basic":
            return None
        if not self._config.basic_auth_user or self._config.basic_auth_password is None:
            raise AuthenticationError("Basic auth selected but credentials are incomplete.")
        return (self._config.basic_auth_user, self._config.basic_auth_password)


# --------------------------------------------------------------------------- #
# Response Parser
# --------------------------------------------------------------------------- #

class ResponseParser:
    """Normalizes an HTTP response into records + metadata (FR-003, FR-007)."""

    def __init__(self, records_path: Optional[str] = None, total_path: Optional[str] = None):
        self._records_path = records_path
        self._total_path = total_path

    def parse(self, response: requests.Response) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise APIResponseError(
                f"Response body is not valid JSON: {exc}", status_code=response.status_code
            ) from exc

        records = self._extract_records(payload)
        metadata = {
            "status_code": response.status_code,
            "url": response.url,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "response_headers": dict(response.headers),
        }

        total = self._extract_total(payload)
        if total is not None:
            metadata["total"] = total

        return records, metadata

    def _extract_records(self, payload: Any) -> list[dict[str, Any]]:
        data = self._walk_path(payload, self._records_path)

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]

        raise APIResponseError(
            "Normalized response payload is neither a list nor an object."
        )

    def _extract_total(self, payload: Any) -> Optional[int]:
        if not self._total_path:
            return None
        try:
            value = self._walk_path(payload, self._total_path)
        except APIResponseError:
            return None
        return value if isinstance(value, int) else None

    @staticmethod
    def _walk_path(payload: Any, path: Optional[str]) -> Any:
        data = payload
        if not path:
            return data
        for key in path.split("."):
            if isinstance(data, dict) and key in data:
                data = data[key]
            else:
                raise APIResponseError(
                    f"path '{path}' not found in response payload."
                )
        return data


# --------------------------------------------------------------------------- #
# HTTP execution (single request with retries)
# --------------------------------------------------------------------------- #

class _HTTPExecutor:
    """Executes a single request with retry handling (FR-006, Section 12)."""

    def __init__(self, config: APIExtractorConfig, session: requests.Session):
        self._config = config
        self._session = session

    def execute(self, request_kwargs: dict[str, Any]) -> requests.Response:
        attempt = 0
        last_exception: Optional[Exception] = None

        while attempt <= self._config.max_retries:
            attempt += 1
            try:
                logger.info(
                    "API request attempt=%s method=%s url=%s",
                    attempt, request_kwargs["method"], request_kwargs["url"],
                )
                response = self._session.request(**request_kwargs)
            except requests.exceptions.RequestException as exc:
                last_exception = exc
                logger.warning("API request failed attempt=%s error=%s", attempt, exc)
                if attempt > self._config.max_retries:
                    break
                self._sleep_before_retry(attempt)
                continue

            if response.status_code in (401, 403):
                logger.error("API authentication failed status=%s", response.status_code)
                raise AuthenticationError(
                    f"Authentication failed with status {response.status_code}."
                )

            if response.ok:
                logger.info("API request succeeded status=%s", response.status_code)
                return response

            if response.status_code in self._config.retry_status_codes and attempt <= self._config.max_retries:
                logger.warning(
                    "API request got retryable status=%s attempt=%s",
                    response.status_code, attempt,
                )
                self._sleep_before_retry(attempt)
                continue

            logger.error("API request failed status=%s", response.status_code)
            raise APIResponseError(
                f"Non-success response status {response.status_code}: {self._safe_body(response)}",
                status_code=response.status_code,
            )

        raise APIRequestError(
            f"Request failed after {self._config.max_retries + 1} attempt(s): {last_exception}"
        )

    def _sleep_before_retry(self, attempt: int) -> None:
        delay = self._config.retry_backoff_seconds * (2 ** (attempt - 1))
        time.sleep(delay)

    @staticmethod
    def _safe_body(response: requests.Response) -> str:
        try:
            return response.text[:300]
        except Exception:  # pragma: no cover - defensive only
            return "<unreadable body>"


# --------------------------------------------------------------------------- #
# Pagination Handler
# --------------------------------------------------------------------------- #

class PaginationHandler:
    """Walks pagination until results run out (FR-004).

    Supports two styles, controlled by ``config.pagination_style``:

    - "page": ``page_param`` is sent as a literal page number that increments
      by 1 each request (e.g. ?page=1, ?page=2, ?page=3 ...), starting from
      ``config.start_page``.
    - "offset": ``page_param`` is sent as a running offset/skip value that
      increments by ``page_size`` each request (e.g. ?skip=0, ?skip=10,
      ?skip=20 ...), starting from ``config.start_page``. This matches APIs
      like dummyjson.com that use skip/limit pagination.

    Stopping conditions (checked in order, after each successful page):
    1. Pagination disabled (no page_size) -> always a single request.
    2. ``total`` is known from response metadata (via ``total_path``) and the
       number of records fetched so far has reached/exceeded it.
    3. The page returned fewer records than ``page_size`` (short/last page),
       or zero records.
    4. ``max_pages`` has been reached.
    """

    def __init__(
        self,
        config: APIExtractorConfig,
        request_builder: RequestBuilder,
        response_parser: ResponseParser,
        executor: _HTTPExecutor,
    ):
        self._config = config
        self._request_builder = request_builder
        self._response_parser = response_parser
        self._executor = executor

    def run(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        all_records: list[dict[str, Any]] = []
        all_page_metadata: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        page_index = 0  # 0-based count of pages fetched so far, NOT the raw API param

        while True:
            page_params = self._page_params(page_index)
            page_label = page_params.get(self._config.page_param, page_index)

            try:
                request_kwargs = self._request_builder.build(page_params)
                response = self._executor.execute(request_kwargs)
                records, metadata = self._response_parser.parse(response)
            except APIResponseError as exc:
                errors.append({"page": page_label, "error": str(exc), "status_code": exc.status_code})
                break
            except APIRequestError as exc:
                errors.append({"page": page_label, "error": str(exc)})
                break
            # AuthenticationError is intentionally NOT caught here: bad credentials
            # are a hard failure for the whole run, not a per-page data issue, so
            # it propagates to the caller per the APIExtractor public interface.

            metadata["page"] = page_label
            all_page_metadata.append(metadata)
            all_records.extend(records)
            page_index += 1

            logger.info(
                "Pagination progress page=%s records_fetched=%s total_so_far=%s",
                page_label, len(records), len(all_records),
            )

            if not self._config.page_size:
                break  # pagination disabled, single request only

            total = metadata.get("total")
            if isinstance(total, int) and len(all_records) >= total:
                break  # API told us how many records exist and we have them all

            if not records or len(records) < self._config.page_size:
                break  # last page reached (short or empty page)

            if self._config.max_pages and page_index >= self._config.max_pages:
                break

        return all_records, all_page_metadata, errors

    def _page_params(self, page_index: int) -> dict[str, Any]:
        if not self._config.page_size:
            return {}

        if self._config.pagination_style == "offset":
            value = self._config.start_page + page_index * self._config.page_size
        else:  # "page"
            value = self._config.start_page + page_index

        return {
            self._config.page_param: value,
            self._config.page_size_param: self._config.page_size,
        }


# --------------------------------------------------------------------------- #
# Public extractor
# --------------------------------------------------------------------------- #

class APIExtractor(Extractor):
    """Extracts records from a configured REST API endpoint.

    Usage (no arguments, config loaded from environment / .env):
        result = APIExtractor().extract()

    Usage (page-number pagination):
        config = APIExtractorConfig(base_url="https://api.example.com", endpoint="users",
                                     auth_type="bearer", auth_token="...",
                                     page_size=50, records_path="data")
        result = APIExtractor(config).extract()

    Usage (offset/skip pagination, e.g. dummyjson.com):
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
        result = APIExtractor(config).extract()
    """

    def __init__(self, config: Optional[APIExtractorConfig] = None, session: Optional[requests.Session] = None):
        self._config = config or APIExtractorConfig.from_env()
        self._session = session or requests.Session()
        self._request_builder = RequestBuilder(self._config)
        self._response_parser = ResponseParser(self._config.records_path, self._config.total_path)
        self._executor = _HTTPExecutor(self._config, self._session)
        self._pagination_handler = PaginationHandler(
            self._config, self._request_builder, self._response_parser, self._executor
        )

    def extract(self) -> ExtractionResult:
        logger.info("Starting API extraction url=%s", self._config.request_url())

        records, page_metadata, errors = self._pagination_handler.run()

        total_found = len(records)

        # If a source_name is provided, consult the etl_metadata table to
        # determine the last processed id/timestamp and filter out already
        # processed records. The loader is responsible for advancing the
        # checkpoint after a successful load.
        skipped = 0
        new_records = records
        if self._config.source_name:
            conn = None
            try:
                conn = db_utils._connect()
                db_utils.ensure_etl_metadata_table(conn)
                meta = db_utils.get_metadata(conn, self._config.source_name)
                if meta and meta.get("last_processed_id") is not None:
                    last_id = meta.get("last_processed_id")
                    try:
                        last_id_val = int(last_id)
                    except Exception:
                        last_id_val = last_id

                    sid = self._config.source_id_field
                    filtered = []
                    for r in records:
                        try:
                            val = r.get(sid)
                        except Exception:
                            val = None
                        # compare numerically if last_id was numeric
                        try:
                            use_val = int(val)
                        except Exception:
                            use_val = val

                        if last_id_val is None or use_val is None:
                            filtered.append(r)
                        else:
                            try:
                                if use_val > last_id_val:
                                    filtered.append(r)
                                else:
                                    skipped += 1
                            except Exception:
                                filtered.append(r)

                    new_records = filtered
            finally:
                if conn:
                    conn.close()

        logger.info("API extraction summary total_found=%s new=%s skipped=%s", total_found, len(new_records), skipped)

        metadata = {
            "source": self._config.request_url(),
            "pages_fetched": len(page_metadata),
            "record_count": len(new_records),
            "pages": page_metadata,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }

        if errors:
            logger.warning("API extraction completed with errors count=%s", len(errors))
        else:
            logger.info("API extraction completed records=%s", len(records))

        return ExtractionResult(records=new_records, metadata=metadata, errors=errors)