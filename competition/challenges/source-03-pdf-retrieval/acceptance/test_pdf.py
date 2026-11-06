from __future__ import annotations

import importlib
import io
from pathlib import Path

import pytest
from powercontext.sources import SourceDefinitionRegistry
from powercontext.sources.pdf_pages import PDF_PAGE_SOURCE_DEFINITION, PDFDocumentInput, parse_pdf_document
from pypdf import PdfWriter

checks = importlib.import_module("challenges.source-03-pdf-retrieval.acceptance.checks")


async def test_complete_public_report(tmp_path: Path) -> None:
    metrics = await checks.check_documents(Path(__file__).parents[1] / "workload/public", tmp_path)
    assert metrics["documents"] == 1 and metrics["pages"] == 503 and metrics["questions"] == 5


async def test_blank_pages_and_document_revisions() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.add_blank_page(width=100, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    original = parse_pdf_document(PDFDocumentInput(document_id="control", content=buffer.getvalue()))
    assert [(page.page_number, page.text) for page in original] == [(1, ""), (2, "")]
    writer.add_metadata({"/Title": "New revision"})
    buffer = io.BytesIO()
    writer.write(buffer)
    updated = parse_pdf_document(PDFDocumentInput(document_id="control", content=buffer.getvalue()))
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    assert (await registry.resolve(original[0])).name != (await registry.resolve(updated[0])).name


def test_empty_password_permission_marker_is_readable() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.encrypt(user_password="", owner_password="owner")
    buffer = io.BytesIO()
    writer.write(buffer)
    pages = parse_pdf_document(PDFDocumentInput(document_id="permissions", content=buffer.getvalue()))
    assert len(pages) == 1 and pages[0].page_number == 1


@pytest.mark.parametrize("raw", [b"", b"not a PDF", b"%PDF-1.7\ntruncated"])
def test_corrupt_documents_fail_atomically(raw) -> None:
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="invalid", content=raw))
