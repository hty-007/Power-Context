from __future__ import annotations

import json
from pathlib import Path

from powercontext.builtin.runtime import GetMemoryEntryRequest, open_builtin_runtime
from powercontext.sources import SourceDefinitionRegistry, project_source_for_transport
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    parse_pdf_document,
    search_pdf_pages,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY
from selftest_support.source_acceptance import persist


async def check_documents(workload: Path, output: Path) -> dict:
    cases = json.loads((workload / "cases.json").read_text())["cases"]
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    observations, all_pages = [], {}
    for case in cases:
        pages = parse_pdf_document(
            PDFDocumentInput(document_id=case["document_id"], content=(workload / case["file"]).read_bytes())
        )
        assert isinstance(pages, tuple)
        assert [page.page_number for page in pages] == list(range(1, case["page_count"] + 1))
        assert all(page.document_id == case["document_id"] for page in pages)
        assert all(page.document_sha256 == case["sha256"] for page in pages)
        for page in pages:
            source = await registry.resolve(page)
            assert await registry.read(source) == page
            assert (await registry.resolve(page)).name == source.name
            observation = project_source_for_transport(registry, source)
            text = observation.projection(TEXT_EVIDENCE_PROJECTION_KEY)
            assert text["source_type"] == "pdf-page"
            assert text["source_id"] == source.name
            assert text["content"] == f"{page.document_id}\n{page.text}"
            assert text["metadata"] == {
                "document_id": page.document_id,
                "document_sha256": page.document_sha256,
                "page_number": page.page_number,
            }
            observations.append(observation)
            all_pages[(page.document_id, page.page_number)] = page.text
    assert len({source.name for source in observations}) == len(observations)
    config, scope_id, stored = await persist(output, PDF_PAGE_SOURCE_DEFINITION, observations)
    queries = []
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope_id)
        for case in cases:
            for question in case["questions"]:
                citations = await search_pdf_pages(memory, question["question"], limit=20)
                assert isinstance(citations, tuple) and len(citations) <= 20
                locations = set()
                for citation in citations:
                    entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                    assert entry.sources
                    for reference in entry.sources:
                        source = stored[reference.source_id]
                        text = source.projection(TEXT_EVIDENCE_PROJECTION_KEY)
                        assert entry.text == text["content"].strip()
                        metadata = text["metadata"]
                        locations.add((metadata["document_id"], metadata["page_number"]))
                assert len(locations) == len(citations), "return distinct exact page citations"
                queries.append({"query_id": question["financebench_id"], "returned_citations": len(citations)})
    metrics = {"documents": len(cases), "pages": len(all_pages), "questions": len(queries), "queries": queries}
    print(json.dumps(metrics, sort_keys=True), flush=True)
    return metrics
