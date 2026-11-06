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

from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, SourceDefinitionRegistry, TextEvidence
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
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
