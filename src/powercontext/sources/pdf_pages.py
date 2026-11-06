# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Complete physical PDF pages and offline retrieval of their exact Memory citations."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from collections import Counter
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, Field, field_validator
from pypdf import PdfReader

from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryHit
from powercontext.builtin.runtime.models import GetMemoryEntryRequest, SearchMemoryRequest
from powercontext.sources.definitions import SourceDefinition, SourceProjection
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import ScopedMemoryApplication


class PDFDocumentInput(BaseModel):
    """The complete original PDF bytes and a caller-owned document identifier."""

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def require_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("PDF document_id must not be empty or blank")  # noqa: TRY003
        return value


class PDFPageInput(BaseModel):
    """A physical page bound to the SHA-256 digest of the entire original PDF."""

    document_id: str
    document_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    page_number: Annotated[int, Field(ge=1, strict=True)]
    text: str

    @field_validator("document_id")
    @classmethod
    def require_document_id(cls, value: str) -> str:
        return PDFDocumentInput.require_document_id(value)


class PDFPageSource(Source):
    """Captured page text with revision-specific physical document coordinates."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse every physical page atomically, including blank and empty-password pages."""
    if not value.content.startswith(b"%PDF-"):
        raise ValueError("Input must contain a complete PDF document")  # noqa: TRY003
    digest = hashlib.sha256(value.content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(value.content), strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a non-empty password")  # noqa: TRY003, TRY301
        return tuple(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=digest,
                page_number=number,
                text=page.extract_text() or "",
            )
            for number, page in enumerate(reader.pages, start=1)
        )
    except Exception as error:
        raise ValueError("Unable to completely parse PDF document") from error  # noqa: TRY003


class _PDFPageProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class: type[PDFPageSource] = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pdf-page",
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


class _PDFPageSourceDefinition:
    name = "pdf-page"
    version = "1"
    input_class: type[PDFPageInput] = PDFPageInput
    source_class: type[PDFPageSource] = PDFPageSource
    projections: tuple[SourceProjection[PDFPageSource], ...] = (_PDFPageProjection(),)

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        components = json.dumps([value.document_id, value.document_sha256, value.page_number], ensure_ascii=False)
        identity = hashlib.sha256(components.encode("utf-8")).hexdigest()
        return PDFPageSource(
            name=f"pdf:{identity}",
            materialization=SourceMaterialization.CAPTURED,
            **value.model_dump(),
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput.model_validate(source.model_dump())


PDF_PAGE_SOURCE_DEFINITION: SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = _PDFPageSourceDefinition()

_FUNCTION_WORDS = frozenset([
    "a",
    "an",
    "the",
    "and",
    "or",
    "for",
    "of",
    "to",
    "in",
    "on",
    "at",
    "by",
    "from",
    "with",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "what",
    "which",
    "who",
    "how",
    "why",
    "when",
    "where",
    "do",
    "does",
    "did",
    "can",
    "could",
    "should",
    "would",
    "has",
    "have",
    "had",
    "it",
    "its",
    "their",
    "this",
    "that",
    "these",
    "those",
    "any",
    "only",
    "if",
    "then",
    "than",
    "not",
    "using",
    "use",
    "used",
    "answer",
    "respond",
    "response",
    "give",
    "state",
    "assuming",
    "assume",
    "perspective",
    "details",
    "shown",
    "question",
    "analyst",
    "investment",
    "usd",
    "millions",
    "million",
    "calculate",
    "define",
    "reported",
    "directly",
    "primarily",
    "quantity",
    "important",
    "materially",
    "outlined",
    "relying",
    "based",
    "according",
])

# General accounting vocabulary expands lexical recall without document-specific facts.
_ACCOUNTING_TERMS = (
    frozenset({"ebitda", "operating", "income", "depreciation", "amortization"}),
    frozenset({"capex", "capital", "expenditures", "additions", "property", "plant", "equipment"}),
    frozenset({"geographies", "geographic", "geographical", "geography", "countries", "regions", "segments"}),
    frozenset({"lawsuits", "litigation", "legal", "proceedings", "battles", "contingencies"}),
    frozenset({"restructuring", "reorganization", "charges", "impairment"}),
    frozenset({"revenue", "revenues", "sales", "turnover"}),
    frozenset({"cash", "flows", "cashflow", "activities"}),
    frozenset({"profit", "earnings", "income", "operations"}),
    frozenset({"balance", "assets", "liabilities", "equity", "position"}),
)


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z]+|\d+", text.casefold()))


def _stem(term: str) -> str:
    if len(term) > 4 and term.endswith("ies"):
        return term[:-3] + "y"
    if len(term) > 3 and term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term


def _query_terms(question: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    terms = tuple(dict.fromkeys(term for term in _tokens(question) if term not in _FUNCTION_WORDS))
    expanded: set[str] = set()
    for group in _ACCOUNTING_TERMS:
        if set(terms) & group:
            expanded.update(group)
    return terms, tuple(sorted(expanded - set(terms)))


def _section_bonus(text: str, query_terms: set[str]) -> float:
    """Match generic report section names when a question uses accounting synonyms."""
    normalized = " ".join(_tokens(text))
    headings = " ".join(_tokens(text[:1200]))
    sections = (
        (
            {"income", "ebitda", "earnings", "profit"},
            ("statement of income", "statements of income", "statements of operations", "statement of operations"),
        ),
        ({"cash", "ebitda", "capex"}, ("statement of cash flows", "statements of cash flows")),
        (
            {"balance", "assets", "liabilities", "equity"},
            ("balance sheet", "balance sheets", "statement of financial position"),
        ),
    )
    bonus = sum(
        6.0
        for triggers, phrases in sections
        if query_terms & triggers and any(phrase in headings for phrase in phrases)
    )
    concepts = (
        ({"legal", "battles", "lawsuits", "litigation"}, ("legal proceedings", "litigation", "legal contingencies")),
        (
            {"geographies", "geographic", "geography", "regions"},
            ("geographic information", "geographical", "reportable segments", "operating segments"),
        ),
    )
    return bonus + sum(
        4.0
        for triggers, phrases in concepts
        if query_terms & triggers and any(phrase in normalized for phrase in phrases)
    )


def _rank_pages(hits: list[MemoryHit], terms: tuple[str, ...], expanded: tuple[str, ...]) -> list[MemoryHit]:
    counts = [Counter(_stem(term) for term in _tokens(hit.text)) for hit in hits]
    frequencies = Counter(term for count in counts for term in count)
    average_length = sum(sum(count.values()) for count in counts) / max(1, len(counts))
    weights = dict.fromkeys((_stem(term) for term in expanded), 0.8)
    weights.update(dict.fromkeys((_stem(term) for term in terms), 1.0))
    for term in weights:
        if term.isdigit() or term == "fy":
            weights[term] = 0.1
    query_terms = set(terms)

    def score(index: int) -> float:
        count = counts[index]
        length = sum(count.values())
        normalization = 1.2 * (0.25 + 0.75 * length / max(1, average_length))
        relevance = 0.0
        for term, weight in weights.items():
            frequency = count[term]
            if frequency:
                inverse = math.log1p((len(hits) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
                relevance += weight * inverse * frequency * 2.2 / (frequency + normalization)
        document_terms = set(_tokens(hits[index].text.partition("\n")[0])) - {"10", "k", "report", "annual"}
        # Prefer the company and reporting period explicitly present in the question.
        relevance += 4.0 * len(document_terms & query_terms)
        return relevance + _section_bonus(hits[index].text, query_terms)

    return [hits[index] for index in sorted(range(len(hits)), key=lambda index: (-score(index), index))]


async def search_pdf_pages(
    memory: ScopedMemoryApplication, question: str, limit: int = 10
) -> tuple[MemoryCitation, ...]:
    """Retrieve and rank real scoped Memory entries, returning distinct PDF page citations."""
    if type(limit) is not int or limit < 0:
        raise ValueError("PDF search limit must be a non-negative integer")  # noqa: TRY003
    if not question.strip():
        raise ValueError("PDF search question must not be blank")  # noqa: TRY003
    if limit == 0:
        return ()
    terms, expanded = _query_terms(question)
    queries = [question, " ".join(terms)]
    queries.extend(term for term in terms if term.isalpha() and len(term) > 3)
    queries.extend(" ".join(sorted(group)) for group in _ACCOUNTING_TERMS if set(terms) & group)
    candidates: dict[tuple[str, str, int, str, str], MemoryHit] = {}
    for query in dict.fromkeys(queries):
        if not query.strip():
            continue
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=max(100, limit * 8)))
        for hit in page.hits:
            key = (
                hit.memory_ref.family,
                hit.memory_ref.artifact_id,
                hit.memory_ref.revision,
                hit.entry_id,
                hit.entry_version_id,
            )
            candidates.setdefault(key, hit)
    citations: list[MemoryCitation] = []
    selected_pages: set[str] = set()
    for hit in _rank_pages(list(candidates.values()), terms, expanded):
        citation = MemoryCitation(
            memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
        )
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        pages = {source.source_id for source in entry.sources if source.source_type == "pdf-page"}
        if not pages or selected_pages & pages:
            continue
        citations.append(citation)
        selected_pages.update(pages)
        if len(citations) == limit:
            break
    return tuple(citations)


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "search_pdf_pages",
]
