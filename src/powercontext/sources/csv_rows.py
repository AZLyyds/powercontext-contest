# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CSV row Sources: one captured Source per primary-keyed table record."""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel, JsonValue, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME = "csv-row"


class CSVDocumentInput(BaseModel):
    """Offline CSV bytes plus the document and primary-key identity."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One validated CSV data row ready for Source materialization."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_row_shape(self) -> CSVRowInput:
        if len(self.columns) != len(self.values):
            raise ValueError("columns and values must have the same length")
        return self


class CSVRowSource(Source):
    """Captured Source for one CSV row, addressable by document + primary key."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the stable ``csv:{doc}:{pk}`` identity with percent-encoded parts."""

    if not isinstance(document_id, str) or not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be a non-empty string without surrounding whitespace")
    if not isinstance(primary_key_value, str):
        raise ValueError("primary_key_value must be a string")
    identity = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")
    return identity


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse and validate an entire offline CSV document before returning rows."""

    document_id = value.document_id
    primary_key = value.primary_key
    if not isinstance(document_id, str) or not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be a non-empty string without surrounding whitespace")
    if not isinstance(primary_key, str) or not primary_key:
        raise ValueError("primary_key must be a non-empty string")

    try:
        text = value.content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV content must be valid UTF-8") from exc

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ValueError("CSV document must include a header row") from exc
    except csv.Error as exc:
        raise ValueError(f"invalid CSV: {exc}") from exc

    if not header or all(cell == "" for cell in header):
        raise ValueError("CSV header must not be empty")

    columns = tuple(header)
    if any(not column or column.strip() == "" for column in columns):
        raise ValueError("CSV header fields must be non-empty and not blank")
    if len(set(columns)) != len(columns):
        raise ValueError("CSV header fields must be unique")
    if primary_key not in columns:
        raise ValueError("primary_key must exactly match a header column name")

    primary_key_index = columns.index(primary_key)
    width = len(columns)
    seen_keys: set[str] = set()
    rows: list[CSVRowInput] = []

    try:
        for record in reader:
            if not record or all(cell == "" for cell in record):
                continue
            if len(record) != width:
                raise ValueError("CSV data rows must match header width")
            values = tuple(record)
            primary_key_value = values[primary_key_index]
            if not primary_key_value or primary_key_value.strip() == "":
                raise ValueError("primary key values must be non-empty and not blank")
            if primary_key_value in seen_keys:
                raise ValueError("primary key values must be unique within a document")
            seen_keys.add(primary_key_value)
            rows.append(
                CSVRowInput(
                    document_id=document_id,
                    primary_key=primary_key,
                    primary_key_value=primary_key_value,
                    columns=columns,
                    values=values,
                )
            )
    except csv.Error as exc:
        raise ValueError(f"invalid CSV: {exc}") from exc

    return tuple(rows)


class CSVRowSourceAdapter:
    """Resolve and read captured CSV row Sources."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Project a CSV row as compact JSON text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = {column: value for column, value in zip(source.columns, source.values, strict=True)}
        content = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        metadata: dict[str, JsonValue] = {
            "document_id": source.document_id,
            "primary_key": source.primary_key,
            "primary_key_value": source.primary_key_value,
            "columns": list(source.columns),
            "row": row,
        }
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=content,
            metadata=metadata,
        )


CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    CSVRowSourceAdapter(),
    version="1",
    projections=(CSVRowTextEvidenceProjection(),),
)

__all__ = [
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "CSV_ROW_SOURCE_DEFINITION",
    "csv_row_source_id",
    "parse_csv_document",
]
