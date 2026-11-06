"""Shared real-runtime fixtures for captured Source acceptance."""

from __future__ import annotations

from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SearchMemoryRequest,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import manifest_for_definition
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


class EvidencePipeline:
    async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="source-evidence",
                text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"],
                sources=(source,),
            )
            for source in request.sources
            if source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"].strip()
        )


async def persist(directory: Path, definition, observations):
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{directory / 'sources.db'}"))
    async with open_builtin_runtime(config) as runtime:
        scope = await runtime.scopes.create(ScopeDraft(title="Sources", summary="Evidence", idempotency_key="sources"))
        await runtime.ingestion.register(manifest_for_definition(definition))
        for observation in observations:
            await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observation))
        await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observations[0]))
    async with open_builtin_runtime(config, candidate_pipeline=EvidencePipeline()) as runtime:
        memory = runtime.memory.for_scope(scope.scope_id)
        for _ in range(len(observations) + 1):
            if not (await memory.flush()).processed:
                break
        else:
            raise AssertionError("flush did not drain the captured sources")
        assert not (await memory.flush()).processed
    async with open_builtin_contexts(config) as contexts:
        journal = await (await contexts.get(scope.scope_id)).sources.journal.entries()
        assert len(journal) == len(observations), "retry must not duplicate an observation"
        stored = {entry.source_ref.source_id: entry.source for entry in journal}
        assert stored == {observation.name: observation for observation in observations}
    return config, scope.scope_id, stored


async def search_sources(memory, query: str, stored: dict, limit: int = 10):
    page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=limit))
    found = []
    for hit in page.hits:
        citation = MemoryCitation(
            memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
        )
        entry = await memory.get(GetMemoryEntryRequest(citation=citation))
        for reference in entry.entry.sources:
            source = stored[reference.source_id]
            assert entry.entry.text == source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"].strip()
            found.append(source)
    return found
