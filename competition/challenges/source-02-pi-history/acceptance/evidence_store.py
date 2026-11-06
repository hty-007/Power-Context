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
    async def extract(self, request, /):
        return tuple(
            MemoryEntryInput(kind=f"session-evidence-{index}", text=part, sources=(source,))
            for source in request.sources
            for index, part in enumerate(self.parts(source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"]))
            if part.strip()
        )

    @staticmethod
    def parts(text):
        # The product limits each Memory entry to 8192 UTF-8 bytes; the Source keeps the full event.
        return tuple(text[index : index + 1500] for index in range(0, len(text), 1500))


async def persist_events(directory, definition, observations):
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{directory / 'sources.db'}"))
    async with open_builtin_runtime(config) as runtime:
        scope = await runtime.scopes.create(ScopeDraft(title="Session", summary="Evidence", idempotency_key="sessions"))
        await runtime.ingestion.register(manifest_for_definition(definition))
        for source in (*observations, observations[0]):
            await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=source))
    async with open_builtin_runtime(config, candidate_pipeline=EvidencePipeline()) as runtime:
        memory = runtime.memory.for_scope(scope.scope_id)
        for _ in range(len(observations) + 1):
            if not (await memory.flush()).processed:
                break
        else:
            raise AssertionError("captured events did not drain")
    async with open_builtin_contexts(config) as contexts:
        journal = (await contexts.get(scope.scope_id)).sources.journal
        entries = await journal.entries()
        stored = {entry.source_ref.source_id: entry.source for entry in entries}
        assert stored == {source.name: source for source in observations}
        assert len(entries) == len(observations)
        for entry in entries:
            assert await (await contexts.get(scope.scope_id)).sources.store.get(entry.source) == entry.source
    return config, scope.scope_id, stored


async def search_events(memory, query, stored):
    page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=100))
    found = []
    for hit in page.hits:
        citation = MemoryCitation(
            memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
        )
        entry = await memory.get(GetMemoryEntryRequest(citation=citation))
        for reference in entry.entry.sources:
            source = stored[reference.source_id]
            full = source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"]
            assert entry.entry.text in {part.strip() for part in EvidencePipeline.parts(full)}
            found.append(source)
    return found
