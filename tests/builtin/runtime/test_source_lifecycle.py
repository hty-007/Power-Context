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

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryEntryInput, MemoryService
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    GetMemoryEntryRequest,
    RememberMemoryRequest,
    SearchTopicMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleError, SourceLifecycleEvent
from powercontext.builtin.scope import ScopeDraft


class _InterruptedWrite(Exception):
    pass


def test_failed_lifecycle_write_rolls_back_native_state_and_can_retry_after_restart(tmp_path, monkeypatch):
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}"))
    withdraw = SourceLifecycleEvent(node_id="root", revision=1, sequence=1, available=False, reason="withdrawn")
    restore = SourceLifecycleEvent(node_id="root", revision=1, sequence=2, available=True, reason="restored")
    forget = MemoryService.forget

    async def fail_after_write(self, *args, **kwargs):
        await forget(self, *args, **kwargs)
        raise _InterruptedWrite

    async def scenario():
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(title="Lifecycle", summary="Test", idempotency_key="lifecycle-rollback")
                )
            ).scope_id
            lifecycle = runtime.lifecycle.for_scope(scope)
            memory = runtime.memory.for_scope(scope)
            topic_memory = runtime.topic_memory.for_scope(scope)
            root = await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="raw", content="Evidence", metadata={})
            )
            await lifecycle.register_source("root", root.source_ref, revision=1)
            remembered = await memory.remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="spectral evidence"),))
            )
            assert remembered.entry is not None
            citation = remembered.entry.citation
            await lifecycle.register_memory("memory", citation, parents=("root",), policy="all")
            topic = await runtime.records.for_scope(scope).create_artifact(
                "topic-memory",
                ArtifactWrite(content={"title": "spectral", "summary": "spectral evidence", "detail": "spectral"}),
            )
            topic_ref = ArtifactRef(family="topic-memory", artifact_id=topic.artifact_id, revision=topic.revision)
            await lifecycle.register_topic("topic", topic_ref, parents=("memory",), policy="all")
            original_head = (await memory.list()).memory_ref
            with monkeypatch.context() as patch:
                patch.setattr(MemoryService, "forget", fail_after_write)
                with pytest.raises(_InterruptedWrite):
                    await lifecycle.apply(withdraw)
            assert (await memory.list()).memory_ref == original_head
            assert await lifecycle.resolve("memory", purpose="generation") == citation
            assert (await topic_memory.search(SearchTopicMemoryRequest(query="spectral"))).hits

        async with open_builtin_runtime(config) as runtime:
            lifecycle = runtime.lifecycle.for_scope(scope)
            memory = runtime.memory.for_scope(scope)
            topic_memory = runtime.topic_memory.for_scope(scope)
            assert await lifecycle.apply(withdraw) == []
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("topic", purpose="generation")
            assert await lifecycle.resolve("topic", purpose="audit") == topic_ref
            assert (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.text == "spectral evidence"
            assert not (await topic_memory.search(SearchTopicMemoryRequest(query="spectral"))).hits
            head = (await memory.list()).memory_ref
            assert await lifecycle.apply(withdraw) == []
            assert (await memory.list()).memory_ref == head
            assert await lifecycle.apply(restore) == ["memory", "root", "topic"]
            assert (await memory.list()).entries[0].entry.entry_version_id == citation.entry_version_id
            assert (await topic_memory.search(SearchTopicMemoryRequest(query="spectral"))).hits

    asyncio.run(scenario())
