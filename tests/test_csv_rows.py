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
import json

import pytest

from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, SourceDefinitionRegistry, TextEvidence
from powercontext.sources.csv_rows import (
    CSV_ROW_SOURCE_DEFINITION,
    CSVDocumentInput,
    CSVRowInput,
    CSVRowSource,
    csv_row_source_id,
    parse_csv_document,
)


def test_unicode_row_survives_serialization_without_normalizing_fields() -> None:
    row = parse_csv_document(
        CSVDocumentInput(
            document_id="机场/名录",
            primary_key="编号",
            content="编号, 名称 ,数量,空值,标记\n 001 ,香港机场,0002,,false\n".encode(),
        )
    )[0]
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    source = asyncio.run(registry.resolve(row))
    restored = CSVRowSource.model_validate_json(source.model_dump_json())
    assert asyncio.run(registry.read(restored)) == row
    evidence = TextEvidence.model_validate(registry.project(restored, TEXT_EVIDENCE_PROJECTION_KEY))
    assert evidence.content == '{"编号":" 001 "," 名称 ":"香港机场","数量":"0002","空值":"","标记":"false"}'
    assert list(json.loads(evidence.content)) == list(row.columns)
    assert evidence.source_id == csv_row_source_id("机场/名录", " 001 ")


@pytest.mark.parametrize(
    "content",
    [
        b"\xef\xbb\xbf",
        b"\nid,name\n1,one\n",
        b"id,name\n1,one\n2,valid\n2,duplicate\n",
        b"id,name\n1,one\n \t,blank\n",
        b"id,name\n1,one\n2,two,extra\n",
        b'id,name\n1,one\n2,"closed"tail\n',
        b'id,name\n1,one\n""\n',
        b"id,name\n1,one\n2,\xff\n",
    ],
)
def test_late_errors_reject_the_entire_document(content: bytes) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id="records", primary_key="id", content=content))


def test_blank_physical_lines_do_not_remove_newlines_inside_a_field() -> None:
    rows = parse_csv_document(
        CSVDocumentInput(document_id="records", primary_key="id", content=b'id,note\n\n1,"a\n\nb"\n\n')
    )
    assert len(rows) == 1
    assert rows[0].values == ("1", "a\n\nb")


def test_strict_csv_still_accepts_quotes_inside_unquoted_fields() -> None:
    rows = parse_csv_document(
        CSVDocumentInput(document_id="records", primary_key="id", content=b'id,note\n1,un"quoted\n')
    )
    assert rows[0].values == ("1", 'un"quoted')


def test_header_names_match_exactly_including_surrounding_spaces() -> None:
    rows = parse_csv_document(CSVDocumentInput(document_id="records", primary_key=" id ", content=b"id, id \n1,2\n"))
    assert rows[0].primary_key_value == "2"
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id="records", primary_key="id", content=b" id \n1\n"))


def test_identity_limit_applies_after_percent_encoding() -> None:
    key = "界" * 27 + "a" * 7
    assert len(csv_row_source_id("d", key)) == 256
    with pytest.raises(ValueError):
        csv_row_source_id("d", key + "a")


@pytest.mark.parametrize("document_id", ["", " ", "\td", "d\n"])
def test_header_only_documents_still_validate_the_document_identity(document_id: str) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id=document_id, primary_key="id", content=b"id,name\n"))


def test_manually_supplied_rows_cannot_disagree_with_their_primary_key() -> None:
    with pytest.raises(ValueError):
        CSVRowInput(
            document_id="records",
            primary_key="id",
            primary_key_value="other",
            columns=("id", "name"),
            values=("1", "one"),
        )
