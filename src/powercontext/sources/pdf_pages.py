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
import math
import re
from collections import Counter
from urllib.parse import quote

from pydantic import BaseModel, JsonValue, field_validator
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.artifacts.search import analyze_fts_query, analyze_text
from powercontext.builtin.runtime.models import SearchMemoryRequest
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PDF_PAGE_SOURCE_NAME = "pdf-page"

# Drop answer-format / persona framing that rarely appears on evidence pages.
# Keep definitional clauses ("Define X as operating income + D&A") — those nouns
# are often the actual filing vocabulary when the metric name itself is absent.
_ANSWER_FRAMING = re.compile(
    r"(?is)\b(?:"
    r"answer\b(?:\s+in\b)?|respond\b|give a response\b|"
    r"assuming the perspective\b|rely(?:ing)? on the details\b|"
    r"by relying on the details\b|who can only use the details\b"
    r").*"
)
_TOKEN = re.compile(r"[a-z]+|\d+", re.IGNORECASE)
_STOP = frozenset(
    """
    a an the and or for of to in on at by from with as is are was were be been being
    what which who how why when where do does did can could should would
    if then than that this these those any all also only into over under about
    not no nor so such same both each few more most other some such
    has have had having
    state give using shown within between during after before
    define calculate using less quantity directly outlined explicitly
    """.split()
)


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


def _content_terms(question: str) -> list[str]:
    """Lexical terms that should appear on evidence pages, not in answer instructions."""

    head = _ANSWER_FRAMING.sub("", question).strip(" \t\r\n.;:")
    if len(_TOKEN.findall(head)) < 4:
        head = question
    # Prefer Analyzer terms so FTS admission and MATCH stay aligned.
    analyzed = [term for term in analyze_fts_query(head).split() if term and term not in _STOP]
    if not analyzed:
        analyzed = [term.casefold() for term in _TOKEN.findall(question) if term.casefold() not in _STOP]
    # Cap term count so admission (ceil(n*0.25)) stays achievable on real pages.
    unique = list(dict.fromkeys(analyzed))
    if len(unique) <= 10:
        return unique
    ranked = sorted(
        unique,
        key=lambda term: (
            0 if term.isdigit() else 1,
            0 if any(ch.isdigit() for ch in term) else 1,
            -len(term),
            term,
        ),
    )
    return ranked[:10]


def _score_page(text: str, terms: list[str], df: Counter[str], docs: int) -> float:
    """BM25-ish score over Analyzer tokens for offline reranking."""

    if not terms or not text:
        return 0.0
    tokens = analyze_text(text).split()
    if not tokens:
        return 0.0
    tf = Counter(tokens)
    avgdl = 500.0
    length = len(tokens)
    score = 0.0
    for term in set(terms):
        freq = tf.get(term, 0)
        if not freq:
            continue
        idf = math.log(1.0 + (docs - df[term] + 0.5) / (df[term] + 0.5))
        denom = freq + 1.2 * (1.0 - 0.75 + 0.75 * length / avgdl)
        score += idf * (freq * 2.2) / denom
    # Soft bonus for covering more distinct query terms.
    covered = sum(1 for term in set(terms) if tf.get(term, 0))
    score += covered * 0.35
    return score


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Search scoped Memory and return distinct exact page citations."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    if type(limit) is not int or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")

    terms = _content_terms(question)
    score_terms = [term for term in dict.fromkeys([*terms, *analyze_fts_query(question).split()]) if term and term not in _STOP]
    fetch = max(limit * 10, 80)
    variants: list[str] = []
    if terms:
        variants.append(" ".join(terms))
        if len(terms) > 5:
            variants.append(" ".join(terms[:5]))
        # Prefer concrete filing nouns over abstract metric aliases.
        concrete = [
            term
            for term in terms
            if term
            not in {
                "unadjusted",
                "adjusted",
                "ebitda",
                "margin",
                "quantity",
                "percent",
                "percentage",
            }
        ]
        if 2 <= len(concrete) <= 8 and " ".join(concrete) not in variants:
            variants.append(" ".join(concrete))
    # Original question remains a useful recall path when aliases are absent from filings.
    variants.append(question.strip())

    hits = []
    seen_keys: set[tuple[str, str, str]] = set()
    for retrieval_query in variants:
        page = await memory.search(SearchMemoryRequest(query=retrieval_query, mode="fts", limit=fetch))
        for hit in page.hits:
            key = (hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)
            if key in seen_keys:
                continue
            hits.append(hit)
            seen_keys.add(key)
        if len(hits) >= fetch:
            break

    df: Counter[str] = Counter()
    analyzed_hits: list[tuple[object, list[str]]] = []
    for hit in hits:
        tokens = analyze_text(hit.text).split()
        analyzed_hits.append((hit, tokens))
        df.update(set(tokens))
    docs = max(len(analyzed_hits), 1)
    ranked = sorted(
        (
            (_score_page(hit.text, score_terms or terms, df, docs), index, hit)
            for index, (hit, _tokens) in enumerate(analyzed_hits)
        ),
        key=lambda item: (-item[0], item[1]),
    )

    citations: list[MemoryCitation] = []
    seen: set[tuple[str, str, str]] = set()
    for _score, _index, hit in ranked:
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
