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

"""PDF page Sources: one captured Source per physical page with scoped retrieval."""

from __future__ import annotations

import hashlib
import io
from urllib.parse import quote

from pydantic import BaseModel, JsonValue, field_validator
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.runtime.models import SearchMemoryRequest
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PDF_PAGE_SOURCE_NAME = "pdf-page"


class PDFDocumentInput(BaseModel):
    """Offline PDF bytes plus a caller-stable document identity."""

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not isinstance(value, str) or not value or value.strip() == "":
            raise ValueError("document_id must be a non-empty string")
        if value != value.strip():
            raise ValueError("document_id must not contain surrounding whitespace")
        return value


class PDFPageInput(BaseModel):
    """One physical PDF page ready for Source materialization."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """Captured Source for one physical PDF page."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Build a stable page identity bounded by ``MAX_SOURCE_ID_LENGTH``."""

    if not isinstance(document_id, str) or not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be a non-empty trimmed string")
    if not isinstance(document_sha256, str) or not document_sha256 or document_sha256 != document_sha256.strip():
        raise ValueError("document_sha256 must be a non-empty trimmed string")
    if type(page_number) is not int or isinstance(page_number, bool) or page_number < 1:
        raise ValueError("page_number must be a positive integer")
    identity = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        # Keep identity unique under the length cap by hashing the long document id.
        digest = hashlib.sha256(f"{document_id}\0{document_sha256}\0{page_number}".encode()).hexdigest()
        identity = f"pdf:{digest}:{page_number}"
        if len(identity) > MAX_SOURCE_ID_LENGTH:
            raise ValueError(f"source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")
    return identity


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of an offline PDF document."""

    document_id = value.document_id
    content = value.content
    if not content:
        raise ValueError("PDF content must not be empty")
    document_sha256 = hashlib.sha256(content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if getattr(reader, "is_encrypted", False):
            # Empty-user-password permission markers are valid input.
            result = reader.decrypt("")
            if result == 0:
                raise ValueError("PDF is encrypted and cannot be read without a password")
        pages: list[PDFPageInput] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001 - surface any page extract failure atomically
                raise ValueError(f"failed to extract text from PDF page {index}") from exc
            pages.append(
                PDFPageInput(
                    document_id=document_id,
                    document_sha256=document_sha256,
                    page_number=index,
                    text=text,
                )
            )
    except PdfReadError as exc:
        raise ValueError(f"invalid or corrupt PDF: {exc}") from exc
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - treat unexpected parse failures as ValueError
        raise ValueError(f"invalid or corrupt PDF: {exc}") from exc
    if not pages:
        raise ValueError("PDF document must include at least one page")
    return tuple(pages)


class PDFPageSourceAdapter:
    """Resolve and read captured PDF page Sources."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Project a PDF page as document-prefixed text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        metadata: dict[str, JsonValue] = {
            "document_id": source.document_id,
            "document_sha256": source.document_sha256,
            "page_number": source.page_number,
        }
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata=metadata,
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDFPageSourceAdapter(),
    version="1",
    projections=(PDFPageTextEvidenceProjection(),),
)


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Search scoped Memory and return distinct exact page citations."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    if type(limit) is not int or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")

    # Over-fetch then collapse to distinct pages; FTS ranks by lexical overlap.
    page = await memory.search(SearchMemoryRequest(query=question, mode="fts", limit=max(limit * 5, limit)))
    citations: list[MemoryCitation] = []
    seen: set[tuple[str, str, str]] = set()
    for hit in page.hits:
        key = (hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
        )
        if len(citations) >= limit:
            break
    return tuple(citations)


__all__ = [
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "PDF_PAGE_SOURCE_DEFINITION",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
