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

"""Scoped Source withdrawal and restoration using immutable evidence references."""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from graphlib import TopologicalSorter
from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.memory import Memory, MemoryEntryVersion, MemoryService
from powercontext.builtin.artifacts.memory.errors import InvalidMemoryCitationError, MemoryEntryNotFoundError
from powercontext.builtin.artifacts.topic_memory import TopicMemory
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.tables import INACTIVE_TOPIC_REVISIONS_TABLE, SOURCE_LIFECYCLE_GRAPHS_TABLE
from powercontext.builtin.sources import validate_scope_id
from powercontext.builtin.statistics import ModelUsagePurpose
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources.models import SourceMaterialization, SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime
    from powercontext.builtin.runtime.relational import RelationalContexts

_Reference = SourceRef | MemoryCitation | ArtifactRef
_Policy = Literal["all", "any"]
_EntryKey = tuple[str, str, str]
_TopicKey = tuple[str, int]


class SourceLifecycleError(ValueError):
    """An invalid Source lifecycle registration, event, or resolution."""

    _messages: ClassVar[dict[str, str]] = {
        "blank": "Expected a nonempty string",
        "storage": "Source lifecycle requires relational storage",
        "source-ref": "Expected a SourceRef",
        "revision": "Source revision must be a positive integer",
        "memory-ref": "Expected a Memory citation",
        "topic-ref": "Expected a Topic Memory reference",
        "source": "Unknown source",
        "revision-mismatch": "Source revision does not match",
        "sequence": "Stale or conflicting source event",
        "purpose": "Invalid resolution purpose",
        "node": "Unknown node",
        "unavailable": "Node is unavailable for generation",
        "registration": "Conflicting registration",
        "parents": "Parents must be registered before their children",
        "captured": "Source must have captured content",
        "reference": "Reference is not readable in this scope",
        "policy": "Invalid dependency policy",
        "no-parents": "Derived nodes require parents",
        "duplicate-parent": "Duplicate parent",
    }

    def __init__(self, code: str, subject: str | None = None) -> None:
        self.code = code
        message = self._messages.get(code, code)
        super().__init__(message if subject is None else f"{message}: {subject!r}")


def _nonblank(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceLifecycleError("blank", field)
    return value


class SourceLifecycleEvent(BaseModel):
    """A versioned availability change to one registered Source."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    node_id: str
    revision: int = Field(gt=0)
    sequence: int = Field(gt=0)
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        return _nonblank(value, info.field_name)


class _Binding(BaseModel):
    reference: _Reference
    revision: int | None = None
    parents: tuple[str, ...] = ()
    policy: _Policy = "all"


class _Graph(BaseModel):
    nodes: dict[str, _Binding] = Field(default_factory=dict)
    events: dict[str, SourceLifecycleEvent] = Field(default_factory=dict)

    def available(self) -> set[str]:
        active: set[str] = set()
        order = TopologicalSorter({node: binding.parents for node, binding in self.nodes.items()})
        for node in order.static_order():
            binding = self.nodes[node]
            if isinstance(binding.reference, SourceRef):
                event = self.events.get(node)
                supported = event is None or event.available
            else:
                parents = (parent in active for parent in binding.parents)
                supported = all(parents) if binding.policy == "all" else any(parents)
            if supported:
                active.add(node)
        return active


def _effective_availability(graph: _Graph) -> tuple[dict[_EntryKey, bool], set[_TopicKey]]:
    """Combine duplicate reference bindings when any node can support the same body."""
    active = graph.available()
    entries: dict[_EntryKey, bool] = {}
    topics: dict[_TopicKey, bool] = {}
    for node, binding in graph.nodes.items():
        ref = binding.reference
        if isinstance(ref, MemoryCitation):
            entry = (ref.memory_ref.artifact_id, ref.entry_id, ref.entry_version_id)
            entries[entry] = entries.get(entry, False) or node in active
        elif isinstance(ref, ArtifactRef):
            topic = (ref.artifact_id, ref.revision)
            topics[topic] = topics.get(topic, False) or node in active
    return entries, {topic for topic, available in topics.items() if not available}


class SourceLifecycleApplication:
    """Select a dependency graph in the built-in relational Runtime."""

    def __init__(self, runtime: BuiltinRuntime, contexts: RelationalContexts | None) -> None:
        self._runtime = runtime
        self._contexts = contexts

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        if self._contexts is None:
            raise SourceLifecycleError("storage")
        return ScopedSourceLifecycle(self._runtime, self._contexts, scope_id)


class ScopedSourceLifecycle:
    """Register dependencies and atomically publish their native visibility."""

    def __init__(self, runtime: BuiltinRuntime, contexts: RelationalContexts, scope_id: str) -> None:
        self._runtime = runtime
        self._contexts = contexts
        self.scope_id = validate_scope_id(scope_id)

    async def register_source(self, node_id: str, source_ref: SourceRef, /, *, revision: int) -> None:
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source-ref")
        if type(revision) is not int or revision < 1:
            raise SourceLifecycleError("revision")
        await self._register(node_id, _Binding(reference=source_ref, revision=revision))

    async def register_memory(
        self, node_id: str, citation: MemoryCitation, /, *, parents: tuple[str, ...], policy: _Policy
    ) -> None:
        if not isinstance(citation, MemoryCitation) or citation.memory_ref.family != Memory.family:
            raise SourceLifecycleError("memory-ref")
        await self._register(node_id, _derived_binding(citation, parents, policy))

    async def register_topic(
        self, node_id: str, artifact_ref: ArtifactRef, /, *, parents: tuple[str, ...], policy: _Policy
    ) -> None:
        if not isinstance(artifact_ref, ArtifactRef) or artifact_ref.family != TopicMemory.family:
            raise SourceLifecycleError("topic-ref")
        await self._register(node_id, _derived_binding(artifact_ref, parents, policy))

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        async with (
            self._runtime._scoped_operation(self.scope_id, embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING),
            self._runtime._locked(self.scope_id),
            self._contexts.database.transaction() as connection,
        ):
            previous = await self._load(connection)
            binding = previous.nodes.get(event.node_id)
            if binding is None or not isinstance(binding.reference, SourceRef):
                raise SourceLifecycleError("source", event.node_id)
            if binding.revision != event.revision:
                raise SourceLifecycleError("revision-mismatch")
            last = previous.events.get(event.node_id)
            if last is not None and event.sequence <= last.sequence:
                if event != last:
                    raise SourceLifecycleError("sequence")
                return sorted(previous.available())
            graph = previous.model_copy(update={"events": {**previous.events, event.node_id: event}})
            reason = unicodedata.normalize("NFC", event.reason).strip()[:512]
            await self._publish(connection, previous, graph, reason)
            return sorted(graph.available())

    async def resolve(self, node_id: str, /, *, purpose: Literal["generation", "audit"]) -> _Reference:
        if purpose not in ("generation", "audit"):
            raise SourceLifecycleError("purpose")
        async with self._runtime._scope_operation(self.scope_id), self._contexts.database.transaction() as connection:
            graph = await self._load(connection)
            if node_id not in graph.nodes:
                raise SourceLifecycleError("node", node_id)
            if purpose == "generation" and node_id not in graph.available():
                raise SourceLifecycleError("unavailable", node_id)
            return graph.nodes[node_id].reference

    async def _register(self, node_id: str, binding: _Binding) -> None:
        _nonblank(node_id, "node_id")
        async with (
            self._runtime._scoped_operation(self.scope_id, embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING),
            self._runtime._locked(self.scope_id),
            self._contexts.database.transaction() as connection,
        ):
            previous = await self._load(connection)
            existing = previous.nodes.get(node_id)
            if existing is not None:
                if existing != binding:
                    raise SourceLifecycleError("registration", node_id)
                return
            if any(parent not in previous.nodes for parent in binding.parents):
                raise SourceLifecycleError("parents")
            await self._validate_reference(connection, binding.reference)
            graph = previous.model_copy(update={"nodes": {**previous.nodes, node_id: binding}})
            await self._publish(connection, previous, graph, "source lifecycle registration")

    async def _validate_reference(self, connection: AsyncConnection, reference: _Reference) -> None:
        repositories = self._contexts.repositories
        try:
            if isinstance(reference, SourceRef):
                source = await repositories.sources.get(connection, self.scope_id, reference)
                if source.value.materialization is not SourceMaterialization.CAPTURED:
                    raise SourceLifecycleError("captured")
            elif isinstance(reference, MemoryCitation):
                memory = self._contexts.lifecycle_memory(self.scope_id, connection)
                await memory.validate_citation(reference)
            else:
                await repositories.topic_memories.get_exact(connection, self.scope_id, reference)
        except (
            ArtifactNotFoundError,
            InvalidMemoryCitationError,
            MemoryEntryNotFoundError,
            RepositoryNotFoundError,
        ) as e:
            raise SourceLifecycleError("reference") from e

    async def _load(self, connection: AsyncConnection) -> _Graph:
        table = SOURCE_LIFECYCLE_GRAPHS_TABLE
        value = await connection.scalar(select(table.c.payload).where(table.c.scope_id == self.scope_id))
        return _Graph() if value is None else _Graph.model_validate_json(value)

    async def _publish(self, connection: AsyncConnection, previous: _Graph, graph: _Graph, reason: str) -> None:
        table = SOURCE_LIFECYCLE_GRAPHS_TABLE
        payload = graph.model_dump_json().encode("utf-8")
        if previous.nodes:
            await connection.execute(update(table).where(table.c.scope_id == self.scope_id).values(payload=payload))
        else:
            await connection.execute(insert(table).values(scope_id=self.scope_id, payload=payload))
        before, formerly_hidden = _effective_availability(previous)
        after, hidden = _effective_availability(graph)
        changes = {key: available for key, available in after.items() if before.get(key) != available}
        if changes:
            service = self._contexts.lifecycle_memory(self.scope_id, connection)
            await _synchronize_memory(service, changes, reason)
        topic_table = INACTIVE_TOPIC_REVISIONS_TABLE
        for artifact_id, revision in sorted(formerly_hidden - hidden):
            await connection.execute(
                delete(topic_table).where(
                    topic_table.c.scope_id == self.scope_id,
                    topic_table.c.artifact_id == artifact_id,
                    topic_table.c.revision == revision,
                )
            )
        if added := sorted(hidden - formerly_hidden):
            await connection.execute(
                insert(topic_table),
                [{"scope_id": self.scope_id, "artifact_id": item, "revision": revision} for item, revision in added],
            )


def _derived_binding(ref: MemoryCitation | ArtifactRef, parents: tuple[str, ...], policy: _Policy) -> _Binding:
    if policy not in ("all", "any"):
        raise SourceLifecycleError("policy")
    if not isinstance(parents, (tuple, list)) or not parents:
        raise SourceLifecycleError("no-parents")
    keys = tuple(_nonblank(parent, "parent") for parent in parents)
    if len(keys) != len(set(keys)):
        raise SourceLifecycleError("duplicate-parent")
    return _Binding(reference=ref, parents=tuple(sorted(keys)), policy=policy)


async def _synchronize_memory(service: MemoryService, changes: dict[_EntryKey, bool], reason: str) -> None:
    by_artifact: dict[str, dict[tuple[str, str], bool]] = defaultdict(dict)
    for (artifact_id, entry_id, version), available in changes.items():
        by_artifact[artifact_id][(entry_id, version)] = available
    for artifact_id, desired in sorted(by_artifact.items()):
        current = await service.head(artifact_id)
        states = {entry.entry_id: entry.state for entry in current.content.manifest.entries}
        retired: list[MemoryEntryVersion] = []
        restored: list[MemoryEntryVersion] = []
        for entry in await service.entries(current):
            active = desired.get((entry.entry_id, entry.entry_version_id))
            if active is None or active == (states[entry.entry_id] == "active"):
                continue
            (restored if active else retired).append(entry)
        if retired:
            current = await service.forget(current, entries=retired, reason=reason)
        if restored:
            await service.reactivate(current, entries=restored, reason=reason)
