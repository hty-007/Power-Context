"""Run query-only candidate inputs and return literal fragment observations."""

from __future__ import annotations

import asyncio
import json
import sys

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    GetMemoryEntryRequest,
    PrepareContextRequest,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.prepared_context import PreparedContextBuilder, PreparedMemoryCandidates
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource


class LiteralPipeline:
    async def extract(self, request: MemoryCandidateRequest, /):
        return tuple(
            MemoryEntryInput(kind="document", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


async def evaluate(inputs):
    observations = []
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=LiteralPipeline()) as runtime:
        for index, case in enumerate(inputs):
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="Evidence selection",
                    summary="Literal document fragments",
                    idempotency_key=f"selection-{index}",
                )
            )
            sources = runtime.sources.for_scope(scope.scope_id)
            memory = runtime.memory.for_scope(scope.scope_id)
            by_text = {document["text"].strip(): document["id"] for document in case["documents"]}
            for document in case["documents"]:
                await sources.capture(
                    CaptureSource(
                        source_id="document-" + document["id"],
                        content=document["text"],
                        metadata={"document_id": document["id"]},
                    )
                )
            while (await memory.flush()).processed:
                pass
            page = await memory.search(SearchMemoryRequest(query=case["query"], mode="fts", limit=16))
            groups = (PreparedMemoryCandidates(scope_id=scope.scope_id, memory_ref=page.memory_ref, hits=page.hits),)
            settings = {"evidence_selection": True} if case["selection"] else {}
            request = PrepareContextRequest(query=case["query"], max_bytes=case["max_bytes"], **settings)
            result = PreparedContextBuilder().build_scopes_result(
                request=request, current_scope_id=scope.scope_id, memory_candidates=groups
            )
            fragments = []
            if case["selection"]:
                for fragment in result.fragments:
                    assert fragment.address.memory.scope_id == scope.scope_id
                    citation = MemoryCitation(
                        memory_ref=fragment.address.memory.artifact,
                        entry_id=fragment.address.entry_id,
                        entry_version_id=fragment.address.entry_version_id,
                    )
                    entry = await memory.get(GetMemoryEntryRequest(citation=citation))
                    text = entry.entry.text
                    assert text in by_text
                    assert type(fragment.start) is int and type(fragment.end) is int
                    assert 0 <= fragment.start < fragment.end <= len(text)
                    assert fragment.text == text[fragment.start : fragment.end]
                    fragments.append(
                        {
                            "document_id": by_text[text],
                            "start": fragment.start,
                            "end": fragment.end,
                            "text": fragment.text,
                        }
                    )
                items = json.loads(result.context.content.splitlines()[-2])["items"] if result.context.content else []
                assert len(items) == len(fragments) == len(result.origins)
                for item, fragment, origin in zip(items, result.fragments, result.origins, strict=True):
                    if isinstance(origin, MemoryCitation):
                        assert origin == MemoryCitation(
                            memory_ref=fragment.address.memory.artifact,
                            entry_id=fragment.address.entry_id,
                            entry_version_id=fragment.address.entry_version_id,
                        )
                    else:
                        assert origin == fragment.address
                    assert item["content"] == fragment.text
                    assert item["start"] == fragment.start and item["end"] == fragment.end
                    assert item["citation"] == {
                        "scope_id": scope.scope_id,
                        "memory_ref": fragment.address.memory.artifact.model_dump(mode="json"),
                        "entry_id": fragment.address.entry_id,
                        "entry_version_id": fragment.address.entry_version_id,
                    }
            elif result.context.content:
                by_id = {hit.entry_id: hit for hit in page.hits}
                for item in json.loads(result.context.content.splitlines()[-2])["items"]:
                    hit = by_id[item["citation"]["entry_id"]]
                    text = (
                        item["content"][:-1] if item["truncated"] and item["content"].endswith("…") else item["content"]
                    )
                    assert hit.text.startswith(text)
                    fragments.append({"document_id": by_text[hit.text], "start": 0, "end": len(text), "text": text})
            content = result.context.content or ""
            assert result.context.content_bytes == len(content.encode("utf-8")) <= case["max_bytes"]
            observations.append(
                {
                    "case_id": case["case_id"],
                    "bytes": len(content.encode("utf-8")),
                    "content": content,
                    "scope_id": scope.scope_id,
                    "fragments": fragments,
                    "candidate_document_ids": [by_text[hit.text] for hit in page.hits],
                }
            )
    return observations


if __name__ == "__main__":
    print(json.dumps(asyncio.run(evaluate(json.load(sys.stdin))), ensure_ascii=False))
