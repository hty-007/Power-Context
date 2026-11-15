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

"""Select literal query evidence within a serialized context's byte budget."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from functools import cache
from itertools import pairwise

from powercontext.builtin.artifacts.search import analyze_fts_query, analyze_text

_SENTENCE_END = re.compile(r"[.!?]+[\"'\u201d\u2019\])]*(?=\s|$)|[\u3002\uff01\uff1f]+|[\r\n]+")
_ABBREVIATION = re.compile(r"(?:\b(?:dr|mr|mrs|vs|etc|fig|no|al|e\.g|i\.e)\.|(?<!\w)[A-Za-z]\.)$", re.IGNORECASE)
_FINDING = re.compile(
    r"\b(?:found|conclud\w*|demonstrat\w*|reveal\w*|associated|correlat\w*|significan\w*|resulted|showed)\b",
    re.IGNORECASE,
)
_AIM = re.compile(r"\b(?:aim(?:ed|s)? to|objective(?: was)? to|assess whether|investigate whether)\b", re.IGNORECASE)
_PASSAGE_CHAR_LIMIT = 520


@dataclass(frozen=True)
class EvidenceSpan:
    document: int
    start: int
    end: int


@dataclass(frozen=True)
class _Passage:
    span: EvidenceSpan
    score: float
    terms: frozenset[str]


@dataclass(frozen=True)
class _Run:
    first: int
    last: int
    span: EvidenceSpan
    slot_bytes: int


def _trimmed_chunk(text: str, start: int, end: int) -> tuple[int, int]:
    chunk = text[start:end]
    return start + len(chunk) - len(chunk.lstrip()), start + len(chunk.rstrip())


def _chunks(text: str, start: int, end: int) -> Iterator[tuple[int, int]]:
    while end - start > _PASSAGE_CHAR_LIMIT:
        ceiling = start + _PASSAGE_CHAR_LIMIT
        split = text.rfind(" ", start + _PASSAGE_CHAR_LIMIT // 2, ceiling)
        boundary = split if split != -1 else ceiling
        yield _trimmed_chunk(text, start, boundary)
        start = boundary
    yield _trimmed_chunk(text, start, end)


def _passage_spans(text: str) -> Iterator[tuple[int, int]]:
    start = 0
    for mark in _SENTENCE_END.finditer(text):
        end = mark.end()
        if mark[0] == "." and _ABBREVIATION.search(text[max(start, end - 12) : end]):
            continue
        yield from _chunks(text, start, end)
        start = end
    yield from _chunks(text, start, len(text))


def _lexical_keys(words: Sequence[str]) -> set[str]:
    return {word[:5] for word in words if word.isascii() and word.isalpha() and len(word) >= 5}


def _query_matches(query: Sequence[str], words: Sequence[str]) -> dict[str, float]:
    exact = set(words)
    prefixes = _lexical_keys(words)
    return {
        word: 1.0 if word in exact else 0.64
        for word in query
        if word in exact or (len(word) >= 5 and word.isascii() and word.isalpha() and word[:5] in prefixes)
    }


def _rank_passages(query: str, documents: Sequence[str]) -> list[list[_Passage]]:
    terms = tuple(dict.fromkeys(analyze_fts_query(query).split()))
    query_pairs = set(pairwise(terms))
    document_matches = [_query_matches(terms, analyze_text(text).split()) for text in documents]
    weights = {
        term: 1 + 0.3 * math.log((len(documents) + 1) / (1 + sum(term in matches for matches in document_matches)))
        for term in terms
    }
    max_weight = sum(weights.values()) or 1.0
    ranked: list[list[_Passage]] = []
    for index, (text, matches) in enumerate(zip(documents, document_matches, strict=True)):
        document_score = sum(weights[term] * value for term, value in matches.items()) / max_weight
        passages: list[_Passage] = []
        for start, end in _passage_spans(text):
            if start >= end:
                continue
            body = text[start:end]
            words = analyze_text(body).split()
            overlap = _query_matches(terms, words)
            phrases = query_pairs.intersection(pairwise(word for word in words if word in weights))
            score = sum(weights[term] * value for term, value in overlap.items()) + 0.20 * len(phrases)
            score *= (0.45 + 0.55 * document_score) * (1 + 0.14 * start / len(text))
            if _FINDING.search(body):
                score *= 1.15
            if _AIM.search(body):
                score *= 0.76
            passages.append(_Passage(EvidenceSpan(index, start, end), score, frozenset(overlap)))
        ranked.append(_with_surrounding_evidence(passages))
    return ranked


def _with_surrounding_evidence(passages: list[_Passage]) -> list[_Passage]:
    """Allow neighboring sentences to explain a finding or supply its missing qualification."""
    group_score = max((passage.score for passage in passages), default=0.0)
    result: list[_Passage] = []
    for i, passage in enumerate(passages):
        neighbors = passages[max(i - 1, 0) : i] + passages[i + 1 : i + 2]
        surrounding = max((neighbor.score for neighbor in neighbors), default=0.0)
        score = passage.score + 0.18 * surrounding + 0.06 * group_score
        result.append(_Passage(passage.span, score, passage.terms))
    return result


def select_evidence(
    query: str,
    documents: Sequence[str],
    *,
    budget: int,
    item_bytes: Callable[[EvidenceSpan], int],
) -> tuple[EvidenceSpan, ...]:
    """Rank sentences, preferring useful neighbors when they can share one citation.

    ``budget`` excludes the outer envelope and includes comma separators between items.
    The caller supplies the real serialized item cost, including escaped text and citation.
    """
    ranked = _rank_passages(query, documents)
    # Account for a comma in each slot. The first slot consumes the extra byte here.
    remaining = budget + 1
    runs: list[list[_Run]] = [[] for _ in documents]
    seen_terms: set[str] = set()

    @cache
    def cost(span: EvidenceSpan) -> int:
        return item_bytes(span) + 1

    while True:
        choice: tuple[int, _Run, list[_Run], frozenset[str], int] | None = None
        best = 0.0
        for document, passages in enumerate(ranked):
            selected = runs[document]
            for index, passage in enumerate(passages):
                if passage.score <= 0 or any(run.first <= index <= run.last for run in selected):
                    continue
                neighbors = [run for run in selected if run.last == index - 1 or run.first == index + 1]
                first = min([index, *(run.first for run in neighbors)])
                last = max([index, *(run.last for run in neighbors)])
                span = EvidenceSpan(document, passages[first].span.start, passages[last].span.end)
                slot = cost(span)
                incremental = slot - sum(run.slot_bytes for run in neighbors)
                if incremental > remaining:
                    continue
                novelty = len(passage.terms - seen_terms) / max(1, len(passage.terms))
                value = passage.score * (1 + 0.30 * novelty) / (180 + max(0, incremental)) ** 0.45
                if value > best:
                    best = value
                    choice = document, _Run(first, last, span, slot), neighbors, passage.terms, incremental
        if choice is None:
            break
        document, new_run, neighbors, terms, incremental = choice
        selected = runs[document]
        for neighbor in neighbors:
            selected.remove(neighbor)
        selected.append(new_run)
        remaining -= incremental
        seen_terms.update(terms)

    return tuple(run.span for selected in runs for run in sorted(selected, key=lambda run: run.first))
