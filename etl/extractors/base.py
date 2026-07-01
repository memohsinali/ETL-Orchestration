"""Shared extractor interface definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ExtractionResult:
    records: list[dict[str, Any]]
    metadata: dict[str, Any]
    errors: list[dict[str, Any]]


class Extractor:
    """Base extractor interface for ETL source modules."""

    def extract(self) -> ExtractionResult:
        """Run the extraction process and return records plus metadata."""
        raise NotImplementedError
