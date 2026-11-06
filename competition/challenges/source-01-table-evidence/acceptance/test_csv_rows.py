from __future__ import annotations

import asyncio
import csv
import io
import json
from pathlib import Path
from urllib.parse import quote

import pytest
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
from powercontext.sources import (
    SourceDefinitionRegistry,
    TextEvidence,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.csv_rows import (
    CSV_ROW_SOURCE_DEFINITION,
    CSVDocumentInput,
    CSVRowInput,
    CSVRowSource,
    csv_row_source_id,
    parse_csv_document,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY

CHALLENGE = Path(__file__).parents[1]
PUBLIC_WORKLOAD = CHALLENGE / "workload" / "public"
PUBLIC_EXPECTED = json.loads((PUBLIC_WORKLOAD / "expected.json").read_text(encoding="utf-8"))


def _parse_public() -> tuple[CSVRowInput, ...]:
    return parse_csv_document(
        CSVDocumentInput(
            document_id=PUBLIC_EXPECTED["document_id"],
            primary_key=PUBLIC_EXPECTED["primary_key"],
            content=(PUBLIC_WORKLOAD / "airports.csv").read_bytes(),
        )
    )


def _row_mapping(columns: list[str], row: dict[str, str]) -> dict[str, str]:
    return {column: row[column] for column in columns}


def test_public_airport_rows_have_exact_source_identity_and_projection() -> None:
    rows = _parse_public()
    assert len(rows) == len(PUBLIC_EXPECTED["rows"]) == 12
    assert len({row.primary_key_value for row in rows}) == len(rows)
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))

    for row, expected_row in zip(rows, PUBLIC_EXPECTED["rows"], strict=True):
        expected_values = tuple(expected_row[column] for column in PUBLIC_EXPECTED["columns"])
        assert row.columns == tuple(PUBLIC_EXPECTED["columns"])
        assert row.values == expected_values
        source = asyncio.run(registry.resolve(row))
        assert isinstance(source, CSVRowSource)
        expected_id = (
            f"csv:{quote(PUBLIC_EXPECTED['document_id'], safe='')}:"
            f"{quote(expected_row[PUBLIC_EXPECTED['primary_key']], safe='')}"
        )
        assert source.source_id == source.name == expected_id
        assert asyncio.run(registry.read(source)) == row

        expected_mapping = _row_mapping(PUBLIC_EXPECTED["columns"], expected_row)
        expected_content = json.dumps(expected_mapping, ensure_ascii=False, separators=(",", ":"))
        evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))
        assert evidence.source_type == "csv-row"
        assert evidence.source_id == expected_id
        assert evidence.content == expected_content
        assert evidence.metadata == {
            "document_id": PUBLIC_EXPECTED["document_id"],
            "primary_key": PUBLIC_EXPECTED["primary_key"],
            "primary_key_value": expected_row[PUBLIC_EXPECTED["primary_key"]],
            "columns": PUBLIC_EXPECTED["columns"],
            "row": expected_mapping,
        }


def test_bom_multiline_escaped_quotes_and_exact_header_matching() -> None:
    content = b'\xef\xbb\xbf" id ",name,note\r\n001,"Airport, East","first line\r\nsecond ""quoted"" line"\r\n'
    rows = parse_csv_document(CSVDocumentInput(document_id="airports:control", primary_key=" id ", content=content))
    assert len(rows) == 1
    assert rows[0].columns == (" id ", "name", "note")
    assert rows[0].values == ("001", "Airport, East", 'first line\r\nsecond "quoted" line')
    assert csv_row_source_id("airports:control", "001") == "csv:airports%3Acontrol:001"


@pytest.mark.parametrize(
    ("document_id", "primary_key", "content"),
    [
        ("airports", "id", b""),
        ("airports", "id", b"id,name\n1,one\n1,duplicate\n"),
        ("airports", "id", b"id,name\n,empty-key\n"),
        ("airports", "id", b"id,name\n1\n"),
        ("airports", "id", b"id,id\n1,1\n"),
        ("airports", "id", b",name\n1,one\n"),
        ("airports", "id", b" \t,name\n1,one\n"),
        ("airports", "missing", b"id,name\n1,one\n"),
        ("airports", "id", b'id,name\n1,"unclosed\n'),
        ("airports", "id", b"id,name\n\xff,invalid utf-8\n"),
    ],
)
def test_invalid_csv_documents_fail_without_partial_rows(
    document_id: str,
    primary_key: str,
    content: bytes,
) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id=document_id, primary_key=primary_key, content=content))


def test_empty_records_are_ignored_and_header_only_documents_are_valid() -> None:
    empty = parse_csv_document(CSVDocumentInput(document_id="empty", primary_key="id", content=b"id,name\n\n"))
    assert empty == ()


def test_source_identity_components_are_delimiter_safe_and_bounded() -> None:
    identity = csv_row_source_id("doc:one", "key/one")
    assert identity == "csv:doc%3Aone:key%2Fone"
    assert identity != csv_row_source_id("doc:two", "key/one")
    assert identity != csv_row_source_id("doc:one", "key:one")
    with pytest.raises(ValueError):
        csv_row_source_id(" doc ", "key")
    with pytest.raises(ValueError):
        csv_row_source_id("doc", "x" * 260)
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id=" doc ", primary_key="id", content=b"id,name\n1,one\n"))


def test_row_identity_survives_reordering_and_non_key_edits() -> None:
    columns = ("id", "name", "note")

    def make_csv(records: list[tuple[str, str, str]]) -> bytes:
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(records)
        return output.getvalue().encode("utf-8")

    original = parse_csv_document(
        CSVDocumentInput(
            document_id="control-table",
            primary_key="id",
            content=make_csv([("01", "Alpha", "first"), ("02", "Beta", "second")]),
        )
    )
    updated = parse_csv_document(
        CSVDocumentInput(
            document_id="control-table",
            primary_key="id",
            content=make_csv([("02", "Beta", "reordered"), ("01", "Alpha edited", "first")]),
        )
    )
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))

    async def resolve_all(rows: tuple[CSVRowInput, ...]) -> dict[str, tuple[str, str]]:
        resolved = {}
        for row in rows:
            source = await registry.resolve(row)
            evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))
            resolved[row.primary_key_value] = (source.source_id, evidence.content)
        return resolved

    original_by_key = asyncio.run(resolve_all(original))
    updated_by_key = asyncio.run(resolve_all(updated))
    assert {key: value[0] for key, value in original_by_key.items()} == {
        key: value[0] for key, value in updated_by_key.items()
    }
    assert original_by_key["01"][1] != updated_by_key["01"][1]


def test_primary_key_values_preserve_surrounding_spaces_but_duplicates_fail() -> None:
    first = parse_csv_document(
        CSVDocumentInput(document_id="space-keys", primary_key="id", content=b"id,name\n key ,first\n")
    )[0]
    second = parse_csv_document(
        CSVDocumentInput(document_id="space-keys", primary_key="id", content=b"id,name\nkey ,second\n")
    )[0]
    assert first.primary_key_value == " key "
    assert second.primary_key_value == "key "
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    first_source = asyncio.run(registry.resolve(first))
    assert first_source.source_id == "csv:space-keys:%20key%20"
    assert csv_row_source_id(first.document_id, first.primary_key_value) != csv_row_source_id(
        second.document_id,
        second.primary_key_value,
    )
    with pytest.raises(ValueError):
        parse_csv_document(
            CSVDocumentInput(
                document_id="space-keys",
                primary_key="id",
                content=b"id,name\n key ,first\n key ,duplicate\n",
            )
        )


def test_public_rows_survive_runtime_restart_and_exact_memory_citation_lookup(tmp_path: Path) -> None:
    rows = _parse_public()
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    observations = [project_source_for_transport(registry, asyncio.run(registry.resolve(row))) for row in rows]
    database = tmp_path / "source-01-table-evidence-airports.db"
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
    scope_id: str | None = None

    class CSVRowPipeline:
        async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
            entries = []
            for source in request.sources:
                projection = source.projection(TEXT_EVIDENCE_PROJECTION_KEY)
                entries.append(MemoryEntryInput(kind="csv-row", text=projection["content"], sources=(source,)))
            return tuple(entries)

    async def submit_rows() -> None:
        nonlocal scope_id
        async with open_builtin_runtime(config) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="Airports", summary="CSV row evidence", idempotency_key="source-01-table-evidence-airports"
                )
            )
            scope_id = scope.scope_id
            await runtime.ingestion.register(manifest_for_definition(CSV_ROW_SOURCE_DEFINITION))
            for observation in observations:
                await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope_id, observation=observation))

    asyncio.run(submit_rows())

    async def retrieve_after_restart() -> None:
        assert scope_id is not None
        async with open_builtin_runtime(config, candidate_pipeline=CSVRowPipeline()) as runtime:
            memory = runtime.memory.for_scope(scope_id)
            flushed = 0
            for _ in range(4):
                result = await memory.flush()
                flushed += result.source_count if result.processed else 0
                if not result.processed:
                    break
            assert flushed == len(rows)

            search = await memory.search(
                SearchMemoryRequest(query=PUBLIC_EXPECTED["search_query"], mode="fts", limit=20)
            )
            target_id = csv_row_source_id(
                PUBLIC_EXPECTED["document_id"],
                PUBLIC_EXPECTED["rows"][0][PUBLIC_EXPECTED["primary_key"]],
            )
            citation = None
            for hit in search.hits:
                candidate = MemoryCitation(
                    memory_ref=hit.memory_ref,
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
                entry = await memory.get(GetMemoryEntryRequest(citation=candidate))
                if any(source.source_id == target_id for source in entry.entry.sources):
                    expected_row = _row_mapping(PUBLIC_EXPECTED["columns"], PUBLIC_EXPECTED["rows"][0])
                    expected_text = json.dumps(expected_row, ensure_ascii=False, separators=(",", ":"))
                    assert entry.entry.text == expected_text
                    citation = candidate
                    break
            assert citation is not None, "public airport row was not retrievable by its evidence query"

        async with open_builtin_contexts(config) as contexts:
            persisted = await (await contexts.get(scope_id)).sources.journal.entries()
            assert len(persisted) == len(rows)
            persisted_by_id = {entry.source_ref.source_id: entry.source for entry in persisted}
            for _row, expected_row in zip(rows, PUBLIC_EXPECTED["rows"], strict=True):
                source_id = csv_row_source_id(
                    PUBLIC_EXPECTED["document_id"],
                    expected_row[PUBLIC_EXPECTED["primary_key"]],
                )
                source = persisted_by_id[source_id]
                evidence = TextEvidence.model_validate(source.projection(TEXT_EVIDENCE_PROJECTION_KEY))
                expected_mapping = _row_mapping(PUBLIC_EXPECTED["columns"], expected_row)
                assert evidence == TextEvidence(
                    source_type="csv-row",
                    source_id=source_id,
                    content=json.dumps(expected_mapping, ensure_ascii=False, separators=(",", ":")),
                    metadata={
                        "document_id": PUBLIC_EXPECTED["document_id"],
                        "primary_key": PUBLIC_EXPECTED["primary_key"],
                        "primary_key_value": expected_row[PUBLIC_EXPECTED["primary_key"]],
                        "columns": PUBLIC_EXPECTED["columns"],
                        "row": expected_mapping,
                    },
                )

    asyncio.run(retrieve_after_restart())
