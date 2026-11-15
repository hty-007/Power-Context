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
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, Field, field_validator
from pypdf import PageObject, PdfReader
from pypdf._cmap import get_encoding
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NumberObject, PdfObject

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


_PDFNumber = NumberObject | FloatObject
_SpaceAdjustment = tuple[ArrayObject, int, _PDFNumber]
_ISOLATED_LETTER = re.compile(r"\b[b-hj-z]\b", re.IGNORECASE)


def _space_width_adjustment(font: DictionaryObject) -> _SpaceAdjustment | None:
    """Read the explicit space's advance for subset fonts affected by pypdf #4113."""
    if font.get("/Subtype") != "/TrueType" or "/Encoding" in font or "/ToUnicode" not in font:
        return None
    _, mapping = get_encoding(font)
    spaces = [ord(code) for code, value in mapping.items() if isinstance(code, str) and len(code) == 1 and value == " "]
    if len(spaces) != 1 or spaces[0] == 32:
        return None
    first_code = font.get("/FirstChar")
    widths = font.get("/Widths")
    if widths is not None:
        widths = widths.get_object()
    if not isinstance(first_code, NumberObject) or not isinstance(widths, ArrayObject):
        return None
    ascii_space, mapped_space = 32 - first_code, spaces[0] - first_code
    if not 0 <= ascii_space < len(widths) or not 0 <= mapped_space < len(widths):
        return None
    old, actual = widths[ascii_space], widths[mapped_space]
    if (
        isinstance(old, (NumberObject, FloatObject))
        and isinstance(actual, (NumberObject, FloatObject))
        and 0 < float(old) < float(actual)
    ):
        return widths, ascii_space, actual
    return None


def _pdf_dict(reference: PdfObject | None) -> DictionaryObject | None:
    value = None if reference is None else reference.get_object()
    return value if isinstance(value, DictionaryObject) else None


def _extract_with_adjusted_spaces(page: PageObject, changes: list[_SpaceAdjustment]) -> str:
    # 6.19's plain extractor measures space using the default encoding before
    # consulting ToUnicode. Adjust only this reader's font data for a retry.
    originals = [(widths, index, widths[index]) for widths, index, _ in changes]
    try:
        for widths, index, width in changes:
            widths[index] = width
        return page.extract_text() or ""
    finally:
        for widths, index, original in originals:
            widths[index] = original


def _extract_page_text(page: PageObject) -> str:
    text = page.extract_text() or ""
    if not text.strip():
        return ""
    fragments = len(_ISOLATED_LETTER.findall(text))
    if fragments < 3:
        return text
    resources = _pdf_dict(page.get("/Resources"))
    if resources is None:
        return text
    fonts = _pdf_dict(resources.get("/Font"))
    if fonts is None:
        return text
    changes = []
    for reference in fonts.values():
        font = reference.get_object()
        if isinstance(font, DictionaryObject) and (adjustment := _space_width_adjustment(font)) is not None:
            changes.append(adjustment)
    if not changes:
        return text

    corrected = _extract_with_adjusted_spaces(page, changes)
    if len(_ISOLATED_LETTER.findall(corrected)) < fragments and re.sub(r"\s", "", corrected) == re.sub(r"\s", "", text):
        return corrected
    return text


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse every physical page atomically, including blank and empty-password pages."""
    if not value.content.startswith(b"%PDF-"):
        raise ValueError("Input must contain a complete PDF document")  # noqa: TRY003
    digest = hashlib.sha256(value.content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(value.content), strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a non-empty password")  # noqa: TRY003, TRY301
        page_tree = _pdf_dict(reader.root_object.get("/Pages"))
        expected_count = None if page_tree is None else page_tree.get("/Count")
        if expected_count is not None:
            expected_count = expected_count.get_object()
        if not isinstance(expected_count, NumberObject) or len(reader.pages) != expected_count:
            raise ValueError("PDF page tree count does not match the readable pages")  # noqa: TRY003, TRY301
        return tuple(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=digest,
                page_number=number,
                text=_extract_page_text(page),
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

_REPORT_DESCRIPTORS = frozenset({"10", "k", "q", "8", "annual", "report", "form", "fy", "fiscal", "year"})
_DISCLOSURE_HEADINGS = {
    "income": (
        r"(?:consolidated )?statements? of (?:income|operations|earnings|profit and loss)",
        r"income statements?|profit and loss statements?",
    ),
    "cash": (r"(?:consolidated )?statements? of cash flows?", r"cash flows? statements?"),
    "balance": (r"(?:consolidated )?balance sheets?", r"statements? of financial position"),
    "equity": (r"statements? of (?:changes in )?(?:shareholders|stockholders) equity",),
    "legal": (r"(?:item 3 )?legal proceedings", r"(?:commitments and )?contingencies", r"litigation"),
    "segments": (
        r"(?:reportable|business|operating) segments?",
        r"segment and geographic information",
        r"(?:geographic|geographical) information",
        r"our divisions",
    ),
}
_EXPLICIT_ROLES = {
    "income": r"\b(?:statements? of (?:income|operations|earnings)|income statements?|p&l)\b",
    "cash": r"\b(?:statements? of cash flows?|cash flows? statements?)\b",
    "balance": r"\b(?:balance sheets?|statements? of financial position)\b",
    "equity": r"\bstatements? of (?:changes in )?(?:shareholders|stockholders)[\u2019']? equity\b",
    "legal": r"\b(?:lawsuits?|litigation|legal (?:battles?|proceedings|contingencies))\b",
    "segments": r"\b(?:geograph\w+|reportable segments?|operating segments?)\b",
}
_INFERRED_ROLES = {
    "income": {"revenue", "revenues", "profit", "earnings", "ebitda", "margin", "cogs"},
    "cash": {"capex", "depreciation", "amortization", "ebitda", "cashflow", "free cash flow"},
    "balance": {"assets", "liabilities", "debt", "inventory", "working capital", "receivables"},
}


@dataclass(frozen=True, slots=True)
class _RankedPage:
    hit: MemoryHit
    relevance: float
    role_scores: dict[str, float]


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


def _document_affinity(document_id: str, question: str) -> float:
    parts = set(_tokens(document_id)) - _REPORT_DESCRIPTORS
    company = {term for term in parts if term.isalpha()} - {"inc", "corp", "company", "plc", "ltd", "llc"}
    terms = set(_tokens(question))
    matched = company & terms
    name_score = len(matched) / len(company) if company else 0.0
    if not matched and company:
        # IDs often join a multi-word issuer name that the question spells with spaces.
        joined_question = "".join(_tokens(question))
        name_score = 0.8 if any(len(term) >= 4 and term in joined_question for term in company) else 0.0
    years = {term for term in parts if re.fullmatch(r"(?:19|20)\d{2}", term)}
    requested_year = next((term for term in _tokens(question) if re.fullmatch(r"(?:19|20)\d{2}", term)), "")
    return name_score * 18 + (3.0 if requested_year in years else 0.0)


def _report_roles(question: str, terms: tuple[str, ...]) -> dict[str, float]:
    query = question.casefold()
    explicit = {role: 1.0 for role, pattern in _EXPLICIT_ROLES.items() if re.search(pattern, query)}
    normalized = " ".join(_tokens(query))
    for role, triggers in _INFERRED_ROLES.items():
        if set(terms) & triggers or any(" " in trigger and trigger in normalized for trigger in triggers):
            explicit.setdefault(role, 0.62)
    return explicit


def _heading_signals(body: str, roles: dict[str, float]) -> tuple[dict[str, float], bool]:
    lines = [line for line in body.splitlines() if line.strip()]
    normalized = [" ".join(_tokens(line)) if len(line) <= 115 else "" for line in lines[:32]]
    early = (*normalized[:8], *(" ".join(pair).strip() for pair in pairwise(normalized[:8])))
    headings = (*normalized, *(" ".join(pair).strip() for pair in pairwise(normalized)))
    # Many filings repeat a "Table of Contents" navigation link on every page.
    # Treat it as an index only when several separate lines look like index entries.
    index_lines = sum(
        bool(re.search(r"\.{3,}|\s{2,}\d{1,4}\s*$|^\s*(?:Item|Part)\s+.+\s\d{1,4}\s*$", line)) for line in lines[:40]
    )
    is_index = index_lines >= 5 and (
        any("table of contents" in heading or heading == "index" for heading in headings)
        or sum(line.lstrip().lower().startswith(("item ", "part ")) for line in lines[:40]) >= 4
    )
    scores: dict[str, float] = {}
    for role, weight in roles.items():
        patterns = _DISCLOSURE_HEADINGS[role]
        if any(re.fullmatch(pattern, heading) for heading in early for pattern in patterns):
            scores[role] = 17.0 * weight
        elif any(re.fullmatch(pattern, heading) for heading in headings for pattern in patterns):
            scores[role] = 7.5 * weight
        elif any(re.search(rf"\b(?:{pattern})\b", heading) for heading in headings for pattern in patterns):
            scores[role] = 3.5 * weight
    return scores, is_index


def _lexical_page_score(
    count: Counter[str],
    frequencies: Counter[str],
    weights: dict[str, float],
    *,
    page_count: int,
    average_length: float,
) -> float:
    length = sum(count.values())
    normalization = 1.2 * (0.25 + 0.75 * length / max(1, average_length))
    relevance = 0.0
    for term, weight in weights.items():
        frequency = count[term]
        if frequency:
            inverse = math.log1p((page_count - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            relevance += weight * inverse * frequency * 2.2 / (frequency + normalization)
    return relevance


def _rank_pages(
    hits: list[MemoryHit], question: str, terms: tuple[str, ...], expanded: tuple[str, ...]
) -> list[MemoryHit]:
    candidates: list[tuple[MemoryHit, str, str]] = []
    for hit in hits:
        document_id, _, body = hit.text.partition("\n")
        if document_id and body.strip():
            candidates.append((hit, document_id, body))
    counts = [Counter(_stem(term) for term in _tokens(body)) for _, _, body in candidates]
    frequencies = Counter(term for count in counts for term in count)
    average_length = sum(sum(count.values()) for count in counts) / max(1, len(counts))
    weights = dict.fromkeys((_stem(term) for term in expanded), 0.45)
    weights.update(dict.fromkeys((_stem(term) for term in terms), 1.0))
    for term in weights:
        if term.isdigit() or term == "fy":
            weights[term] = 0.1
    roles = _report_roles(question, terms)
    query_terms = {_stem(term) for term in terms if term.isalpha()}
    rows: list[_RankedPage] = []
    affinity: dict[str, float] = {}
    for (hit, document_id, body), count in zip(candidates, counts, strict=True):
        relevance = _lexical_page_score(
            count, frequencies, weights, page_count=len(candidates), average_length=average_length
        )
        # A dense passage is better evidence than isolated mentions across multiple columns.
        proximity = max(
            (
                len(query_terms.intersection(_stem(term) for term in _tokens(body[start : start + 400])))
                for start in range(0, len(body), 200)
            ),
            default=0,
        )
        if document_id not in affinity:
            affinity[document_id] = _document_affinity(document_id, question)
        relevance += 0.9 * proximity + affinity[document_id]
        signals, is_index = _heading_signals(body, roles)
        rows.append(_RankedPage(hit, relevance - (12.0 if is_index else 0.0), signals))

    whole_page = sorted(rows, key=lambda row: -(row.relevance + max(row.role_scores.values(), default=0.0)))
    # A question can require several distinct statements. Reserve results for the
    # strongest heading in each asked-for section before filling by global relevance.
    selected: dict[str, MemoryHit] = {}
    for role in roles:
        ranked = sorted(
            (row for row in rows if row.role_scores.get(role, 0.0) >= 5),
            key=lambda row: -(row.relevance + 1.6 * row.role_scores[role]),
        )
        if ranked:
            hit = ranked[0].hit
            selected.setdefault(hit.entry_version_id, hit)
    for row in whole_page:
        selected.setdefault(row.hit.entry_version_id, row.hit)
    return list(selected.values())


async def _pdf_candidates(
    memory: ScopedMemoryApplication, question: str, terms: tuple[str, ...], limit: int
) -> list[MemoryHit]:
    queries = [question, " ".join(terms)]
    queries.extend([term for term in terms if term.isalpha() and len(term) > 3][:12])
    candidates: dict[tuple[str, str, int, str, str], MemoryHit] = {}

    async def collect(query: str, candidate_limit: int, *, document_id: str | None = None) -> None:
        results = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=candidate_limit))
        for hit in results.hits:
            if document_id is not None and hit.text.partition("\n")[0] != document_id:
                continue
            key = (
                hit.memory_ref.family,
                hit.memory_ref.artifact_id,
                hit.memory_ref.revision,
                hit.entry_id,
                hit.entry_version_id,
            )
            candidates.setdefault(key, hit)

    for query in dict.fromkeys(queries):
        if query.strip():
            await collect(query, max(100, limit * 8))
    documents = Counter(hit.text.partition("\n")[0] for hit in candidates.values() if "\n" in hit.text)
    # The projection puts the full ID in every page's index entry. An exact ID
    # search recovers statements whose text doesn't repeat a question's terms.
    document_ids = sorted(
        documents,
        key=lambda document_id: (-_document_affinity(document_id, question), -documents[document_id], document_id),
    )
    for document_id in document_ids[:4]:
        await collect(document_id, max(600, limit * 16), document_id=document_id)
    return list(candidates.values())


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
    candidates = await _pdf_candidates(memory, question, terms, limit)
    citations: list[MemoryCitation] = []
    selected_pages: set[str] = set()
    for hit in _rank_pages(candidates, question, terms, expanded):
        citation = MemoryCitation(
            memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
        )
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        pages = {source.source_id for source in entry.sources if source.source_type == "pdf-page"}
        if len(pages) != 1 or selected_pages & pages:
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
