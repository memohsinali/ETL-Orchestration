from __future__ import annotations

from typing import Dict, Optional

from etl.validators.schemas import (
    APIProductSchema,
    CSVUserRecordSchema,
    MongoUserRecordSchema,
    SourceType,
    UserRecordSchema,
)


class SchemaRegistry:
    def __init__(self) -> None:
        self._registry: Dict[tuple[SourceType, str], type] = {}
        # CSV — matches csv_users.csv column layout
        self.register(SourceType.CSV,   "v1", CSVUserRecordSchema)
        # API — matches dummyjson.com/products response
        self.register(SourceType.API,   "v1", APIProductSchema)
        # Mongo — matches documents written by generate_mongo_users.py
        self.register(SourceType.MONGO, "v1", MongoUserRecordSchema)

    def register(self, source_type: SourceType, schema_version: str, schema_class: type) -> None:
        self._registry[(source_type, schema_version)] = schema_class

    def resolve(self, source_type: SourceType | str, schema_version: str) -> type:
        if not isinstance(source_type, SourceType):
            try:
                source_type = SourceType(source_type)
            except ValueError as exc:
                raise ValueError(f"Unknown source_type={source_type}") from exc

        schema = self._registry.get((source_type, schema_version))
        if schema is None:
            raise ValueError(f"No schema registered for source_type={source_type} version={schema_version}")
        return schema

    def available_versions(self, source_type: SourceType) -> list[str]:
        return [version for (stype, version), _ in self._registry.items() if stype == source_type]
