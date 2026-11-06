from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from powercontext.builtin.artifacts.memory import CapabilityNotSupportedError, EmbeddingProfile, MemoryEntryInput
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.tags import MemoryEntryTagTarget, TagFilter
from sqlalchemy import Engine, event

PROFILE = EmbeddingProfile(
    profile_id="retrieval-02-hybrid-search-test-v1",
    model="deterministic",
    dimension=3,
    distance="l2",
    normalization="unit",
)


class _Embedding:
    profile = PROFILE

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=tuple(
                (1.0, 0.0, 0.0)
                if "alpha" in text.casefold()
                else (0.0, 0.0, 1.0)
                if "gamma" in text.casefold()
                else (0.0, 1.0, 0.0)
                for text in texts
            )
        )


Statement = tuple[str, object]


@contextmanager
def _statements() -> Iterator[list[Statement]]:
    statements: list[Statement] = []

    def record(_connection, _cursor, statement, parameters, _context, _executemany) -> None:
        statements.append((statement, parameters))

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", record)


@pytest.mark.asyncio
async def test_seekdb_executes_one_native_hybrid_candidate_statement() -> None:
    database_path = Path(tempfile.mkdtemp(prefix="retrieval-02-hybrid-search-", dir=os.environ.get("TMPDIR"))) / "db"
    async with open_builtin_contexts(
        BuiltinConfig(database=SeekDBConfig(path=database_path)), embedding_model=_Embedding()
    ) as contexts:
        memory = (await contexts.get("project")).artifacts.memory
        head = await memory.remember(
            memory=None,
            entries=(
                MemoryEntryInput(kind="fact", text="Alpha semantic record."),
                MemoryEntryInput(kind="fact", text="Beta lexical record."),
            ),
            mode="append",
        )
        assert head is not None
        revised_head = await memory.remember(
            memory=head,
            entries=(MemoryEntryInput(kind="fact", text="Gamma entry in the newer memory head."),),
            mode="append",
        )
        other_memory = (await contexts.get("other-scope")).artifacts.memory
        other_head = await other_memory.remember(
            memory=None,
            entries=(MemoryEntryInput(kind="fact", text="Alpha decoy in another scope."),),
            mode="append",
        )
        assert revised_head is not None
        assert other_head is not None
        with _statements() as statements:
            result = await memory.search("alpha", memories=(revised_head,), mode="hybrid", limit=1)

        native = [
            (statement, parameters)
            for statement, parameters in statements
            if "pc_memory_entry_heads" in statement
            and "pc_memory_vector_entries" in statement
            and "AGAINST" in statement.upper()
            and "l2_distance" in statement.lower()
        ]
        assert len(native) == 1
        statement, parameters = native[0]
        assert len(result.hits) == 1
        assert result.hits[0].text == "Alpha semantic record."
        assert result.hits[0].matched_by == ("fts", "vector")
        assert result.hits[0].memory_ref.artifact_id == revised_head.artifact_id
        assert result.hits[0].memory_ref.revision == revised_head.revision
        first_identity = (
            result.hits[0].memory_ref,
            result.hits[0].entry_id,
            result.hits[0].entry_version_id,
        )

        repeated = await memory.search("alpha", memories=(revised_head,), mode="hybrid", limit=1)
        repeated_identity = (
            repeated.hits[0].memory_ref,
            repeated.hits[0].entry_id,
            repeated.hits[0].entry_version_id,
        )
        assert repeated_identity == first_identity

        with pytest.raises(CapabilityNotSupportedError):
            await memory.search("gamma", memories=(head,), mode="hybrid", limit=1)

        isolated = await other_memory.search("alpha", memories=(other_head,), mode="hybrid", limit=1)
        assert len(isolated.hits) == 1
        assert isolated.hits[0].memory_ref.artifact_id == other_head.artifact_id
        assert isolated.hits[0].memory_ref.revision == other_head.revision

        fts = await memory.search("beta", memories=(revised_head,), mode="fts")
        vector = await memory.search("beta", memories=(revised_head,), mode="vector")
        assert fts.hits[0].text == "Beta lexical record."
        assert vector.hits[0].text == "Beta lexical record."

        beta = next(entry for entry in head.content.manifest.entries if entry.entry_id == fts.hits[0].entry_id)
        target = MemoryEntryTagTarget(artifact_id=head.artifact_id, entry_id=beta.entry_id)
        empty = await contexts.records.get_tags("project", target)
        await contexts.records.replace_tags("project", target, ("selected",), expected_etag=empty.etag)
        tagged = await memory.search(
            "beta", memories=(revised_head,), mode="hybrid", limit=1, tag_filter=TagFilter(tags=("selected",))
        )
        assert [hit.entry_id for hit in tagged.hits] == [beta.entry_id]
        alpha = next(entry for entry in await memory.entries(revised_head) if entry.text == "Alpha semantic record.")
        latest = await memory.remember(
            memory=revised_head,
            entries=(MemoryEntryInput(kind="fact", text="Alpha revised semantic record.", entry=alpha),),
            mode="append",
        )
        assert latest is not None
        revised = await memory.search("alpha", memories=(latest,), mode="hybrid", limit=1)
        assert revised.hits[0].entry_id == alpha.entry_id
        assert revised.hits[0].entry_version_id != alpha.entry_version_id
        assert revised.hits[0].text == "Alpha revised semantic record."
        assert revised.hits[0].memory_ref == latest.as_ref()
        current_alpha = next(entry for entry in await memory.entries(latest) if entry.entry_id == alpha.entry_id)
        inactive = await memory.forget(latest, entries=(current_alpha,))
        inactive_hits = await memory.search("alpha", memories=(inactive,), mode="hybrid", limit=10)
        assert all(hit.entry_id != alpha.entry_id for hit in inactive_hits.hits)
        restored = await memory.reactivate(inactive, entries=(current_alpha,))
        recovered = await memory.search("alpha", memories=(restored,), mode="hybrid", limit=1)
        assert recovered.hits[0].entry_version_id == current_alpha.entry_version_id
        assert recovered.hits[0].memory_ref == restored.as_ref()
        unchanged = await memory.search("beta", memories=(restored,), mode="hybrid", limit=1)
        assert unchanged.hits[0].entry_id == beta.entry_id

        async with contexts.database.transaction() as connection:
            plan = "\n".join(
                str(row[0]) for row in (await connection.exec_driver_sql(f"EXPLAIN {statement}", parameters)).all()
            )
        assert "TEXT RETRIEVAL SCAN" in plan
        assert "VECTOR INDEX" in plan
