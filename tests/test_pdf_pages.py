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

from __future__ import annotations

import asyncio
import hashlib
import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    TextEvidence,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    PDFPageInput,
    parse_pdf_document,
    search_pdf_pages,
)


def document(*texts: str, password: str | None = None) -> bytes:
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=300, height=200)
        if text:
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
            })
            content = DecodedStreamObject()
            content.set_data(f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = content
    if password is not None:
        writer.encrypt(user_password=password, owner_password="owner-password")  # noqa: S106
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_all_pages_keep_physical_boundaries_and_revision_digest() -> None:
    content = document("Revenue 1000", "", "Operating income 200")
    pages = parse_pdf_document(PDFDocumentInput(document_id="example", content=content))
    assert [page.page_number for page in pages] == [1, 2, 3]
    assert [page.text for page in pages] == ["Revenue 1000", "", "Operating income 200"]
    assert {page.document_sha256 for page in pages} == {hashlib.sha256(content).hexdigest()}
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    source = asyncio.run(registry.resolve(pages[2]))
    assert asyncio.run(registry.read(source)) == pages[2]
    evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))
    assert evidence.content == "example\nOperating income 200"
    assert evidence.metadata["page_number"] == 3


def test_password_required_and_truncated_pdf_fail_atomically() -> None:
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="private", content=document("Secret", password="required")))  # noqa: S106
    valid = document("First", "Second")
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="truncated", content=valid[: len(valid) // 2]))


def test_inconsistent_page_tree_count_fails_atomically() -> None:
    original = document("First", "Second")
    assert b"/Count 2" in original
    with pytest.raises(ValueError):
        parse_pdf_document(
            PDFDocumentInput(document_id="damaged", content=original.replace(b"/Count 2", b"/Count 3", 1))
        )


def test_retrieval_recovers_both_statements_when_neither_mentions_ebitda() -> None:
    class EvidencePipeline:
        async def extract(self, request, /):
            return tuple(
                MemoryEntryInput(
                    kind="source-evidence",
                    text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"],
                    sources=(source,),
                )
                for source in request.sources
            )

    async def scenario() -> None:
        name = "NORTHSTAR_2025_10K"
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        pages = (
            "This annual filing explains the company's EBITDA policy.",
            "Consolidated Statements of Income\nSales 100. Gross profit 60.",
            "Consolidated Statements of Cash Flows\nCapital spending 12. Purchases 8.",
        )
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()), candidate_pipeline=EvidencePipeline()
        ) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="PDF reports", summary="Two statements", idempotency_key="pdf-retrieval")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for report, digest in (("NORTHSTAR_2024_10K", "b" * 64), (name, "a" * 64)):
                for number, body in enumerate(pages, start=1):
                    page = PDFPageInput(document_id=report, document_sha256=digest, page_number=number, text=body)
                    source = await registry.resolve(page)
                    await runtime.ingestion.submit(
                        SubmitSourceObservation(
                            scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                        )
                    )
            memory = runtime.memory.for_scope(scope.scope_id)
            assert (await memory.flush()).processed
            selections = await search_pdf_pages(memory, "What is Northstar EBITDA in FY2025?", limit=2)
            texts = {(await memory.get(GetMemoryEntryRequest(citation=citation))).entry.text for citation in selections}
            assert texts == {f"{name}\n{body}" for body in pages[1:]}

    asyncio.run(scenario())


@pytest.mark.parametrize("document_id", ["", " ", "\n\t"])
def test_document_id_must_identify_a_document(document_id: str) -> None:
    with pytest.raises(ValueError):
        PDFDocumentInput(document_id=document_id, content=document("Page"))


def test_search_rejects_bad_limits_before_accessing_memory() -> None:
    async def scenario():
        for limit in (-1, True, 1.5):
            with pytest.raises(ValueError):
                await search_pdf_pages(None, "question", limit)
        assert await search_pdf_pages(None, "question", 0) == ()

    asyncio.run(scenario())
