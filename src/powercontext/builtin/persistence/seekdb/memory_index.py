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

"""Combine the existing seekdb full-text and ANN candidate paths in one round trip."""

from __future__ import annotations

from pyobvector import VECTOR
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import TextClause
from typing_extensions import override

from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    MemorySearchChannels,
    MemorySearchRequest,
)
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex, memory_channel_hits
from powercontext.builtin.persistence.oceanbase.memory_index import OceanBaseMemoryFTSIndex, OceanBaseMemoryVectorIndex
from powercontext.builtin.persistence.tags import memory_tag_parameters, memory_tag_sql


def _hybrid_statement(dimension: int, *, tagged: bool) -> TextClause:
    fts_filter = memory_tag_sql("f") if tagged else ""
    vector_filter = memory_tag_sql("m") if tagged else ""
    # Retain each native query's join and top-k plan. Re-sorting vector ties after
    # the top-k can promote different entries than seekdb chose in the vector scan.
    # Only fixed tag predicates are interpolated; request values remain bound.
    sql = f"""
        SELECT 'fts' AS channel, fts.*
        FROM (
            SELECT f.memory_artifact_id, f.head_revision, f.entry_id, f.entry_version_id,
                   v.text, NULL AS distance
            FROM pc_memory_entry_heads AS f
            JOIN pc_memory_entry_versions AS v
              ON v.scope_id = f.scope_id
             AND v.memory_artifact_id = f.memory_artifact_id
             AND v.entry_version_id = f.entry_version_id
            WHERE f.scope_id = :scope_id
              AND f.memory_artifact_id IN :memory_artifact_ids
              AND MATCH(f.searchable_text) AGAINST (:query)
              {fts_filter}
            ORDER BY MATCH(f.searchable_text) AGAINST (:query) DESC,
                     f.memory_artifact_id, f.entry_id, f.entry_version_id
            LIMIT :candidate_limit
        ) AS fts
        UNION ALL
        SELECT 'vector' AS channel, vec.*
        FROM (
            SELECT m.memory_artifact_id, m.head_revision, m.entry_id, m.entry_version_id,
                   v.text, l2_distance(m.embedding, :query_vector) AS distance
            FROM pc_memory_vector_entries AS m
            JOIN pc_memory_entry_versions AS v
              ON v.scope_id = m.scope_id
             AND v.memory_artifact_id = m.memory_artifact_id
             AND v.entry_version_id = m.entry_version_id
            WHERE m.scope_id = :scope_id
              AND m.memory_artifact_id IN :memory_artifact_ids
              {vector_filter}
            ORDER BY l2_distance(m.embedding, :query_vector) APPROXIMATE
            LIMIT :candidate_limit
        ) AS vec
        """  # noqa: S608
    statement = text(sql).bindparams(
        bindparam("memory_artifact_ids", expanding=True),
        bindparam("query_vector", type_=VECTOR(dimension)),
    )
    if tagged:
        return statement.bindparams(bindparam("tag_keys", expanding=True), bindparam("tag_hashes", expanding=True))
    return statement


class SeekDBMemoryIndex(CompositeMemoryIndex):
    """Use the same persistence projections and single-channel searches as OceanBase."""

    def __init__(self, profile: EmbeddingProfile) -> None:
        super().__init__(OceanBaseMemoryFTSIndex(), OceanBaseMemoryVectorIndex(profile))
        self._profile = profile
        self._search_sql = _hybrid_statement(profile.dimension, tagged=False)
        self._tagged_search_sql = _hybrid_statement(profile.dimension, tagged=True)

    @override
    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        if request.mode != "hybrid":
            return await super().search(connection, scope_id, request)
        if request.embedding_profile != self._profile or request.query_vector is None:
            raise CapabilityNotSupportedError("embedding-profile")
        if not await self.vector_complete(connection, scope_id, request.memories, self._profile):
            raise CapabilityNotSupportedError("vector")

        result = await connection.execute(
            self._search_sql if request.tag_filter is None else self._tagged_search_sql,
            {
                "scope_id": scope_id,
                "memory_artifact_ids": tuple(memory.artifact_id for memory in request.memories),
                "candidate_limit": request.candidate_limit,
                "query": request.analyzed_query,
                "query_vector": validate_embedding(request.query_vector, dimension=self._profile.dimension),
                **memory_tag_parameters(request.tag_filter),
            },
        )
        rows = result.mappings().all()
        return MemorySearchChannels(
            fts=memory_channel_hits((row for row in rows if row["channel"] == "fts"), request.memories),
            vector=memory_channel_hits((row for row in rows if row["channel"] == "vector"), request.memories),
        )
