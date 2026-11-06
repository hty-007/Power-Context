from __future__ import annotations

import asyncio

import pytest
from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, RememberMemoryRequest, open_builtin_runtime
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleError, SourceLifecycleEvent
from pydantic import ValidationError

from .checks import assert_runtime, build, event, oracle


def test_dag_propagates_withdrawal_and_restore_to_runtime_and_context(tmp_path):
    async def run():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}"))
        async with open_builtin_runtime(config) as runtime:
            scope, service, bindings, citations = await build(runtime)
            withdrawn = set()
            for root, available in (("root0", False), ("root1", False), ("root0", True)):
                service_sequence = 2 if available else 1
                actual = await service.apply(event(root, service_sequence, available))
                if available:
                    withdrawn.discard(root)
                else:
                    withdrawn.add(root)
                assert actual == sorted(oracle(bindings, withdrawn))
                await assert_runtime(runtime, scope, bindings, citations, oracle(bindings, withdrawn))
            inactive = next(node for node in bindings if node not in oracle(bindings, withdrawn))
            with pytest.raises(SourceLifecycleError):
                await service.resolve(inactive, purpose="generation")
            assert await service.resolve(inactive, purpose="audit") == citations[inactive]
        async with open_builtin_runtime(config) as runtime:
            service = runtime.lifecycle.for_scope(scope)
            await assert_runtime(runtime, scope, bindings, citations, oracle(bindings, withdrawn))
            before = (await runtime.memory.for_scope(scope).list()).memory_ref
            await service.apply(event("root0", 2, True))
            assert (await runtime.memory.for_scope(scope).list()).memory_ref == before
            await service.apply(event("root1", 2, True))
            await assert_runtime(runtime, scope, bindings, citations, oracle(bindings, set()))
            for node_id, reference in citations.items():
                assert await service.resolve(node_id, purpose="generation") == reference

    asyncio.run(run())


def test_event_identity_revision_and_dag_registration_are_strict():
    async def run():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            _, service, _, citations = await build(runtime)
            await service.apply(event("root0", 4, False))
            for invalid in (
                event("root0", 3, True),
                event("root0", 4, True),
                event("missing", 1, True),
                SourceLifecycleEvent(node_id="root0", revision=2, sequence=5, available=True, reason="obsolete"),
            ):
                with pytest.raises(SourceLifecycleError):
                    await service.apply(invalid)
            with pytest.raises(SourceLifecycleError):
                await service.register_memory("cycle", citations["needle0"], parents=("cycle",), policy="all")
            with pytest.raises(SourceLifecycleError):
                await service.register_memory("empty", citations["needle0"], parents=(), policy="any")
            with pytest.raises(SourceLifecycleError):
                await service.register_memory("needle0", citations["needle0"], parents=("root3",), policy="any")

    asyncio.run(run())
    with pytest.raises(ValidationError):
        SourceLifecycleEvent(node_id="x", revision=0, sequence=0, available=False, reason="", extra=True)


def test_new_derivatives_of_withdrawn_evidence_stay_out_of_generation():
    async def run():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope, lifecycle, bindings, citations = await build(runtime)
            await lifecycle.apply(event("root0", 1, False))
            created = await runtime.memory.for_scope(scope).remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="late fixture evidence"),))
            )
            await lifecycle.register_memory("late", created.entry.citation, parents=("root0",), policy="all")
            bindings["late"] = (("root0",), "all", "memory")
            citations["late"] = created.entry.citation
            artifact = await runtime.records.for_scope(scope).create_artifact(
                "topic-memory",
                ArtifactWrite(
                    content={
                        "title": "late-topic",
                        "summary": "late-topic supported summary",
                        "detail": "late",
                    }
                ),
            )
            reference = ArtifactRef(family="topic-memory", artifact_id=artifact.artifact_id, revision=artifact.revision)
            await lifecycle.register_topic("late-topic", reference, parents=("late",), policy="all")
            bindings["late-topic"] = (("late",), "all", "topic")
            citations["late-topic"] = reference
            await assert_runtime(runtime, scope, bindings, citations, oracle(bindings, {"root0"}))
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("late-topic", purpose="generation")
            assert await lifecycle.resolve("late-topic", purpose="audit") == reference
            await lifecycle.apply(event("root0", 2, True))
            await assert_runtime(runtime, scope, bindings, citations, oracle(bindings, set()))

    asyncio.run(run())


def test_shared_topic_withdrawal_preserves_independent_surviving_fact():
    async def run():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope, lifecycle, bindings, citations = await build(runtime)
            for node, parent in (("oldfact", "root0"), ("currentfact", "root1")):
                created = await runtime.memory.for_scope(scope).remember(
                    RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text=f"{node} fixture evidence"),))
                )
                await lifecycle.register_memory(node, created.entry.citation, parents=(parent,), policy="all")
                bindings[node] = ((parent,), "all", "memory")
                citations[node] = created.entry.citation
            artifact = await runtime.records.for_scope(scope).create_artifact(
                "topic-memory",
                ArtifactWrite(
                    content={
                        "title": "mixedsummary",
                        "summary": "mixedsummary supported summary",
                        "detail": "oldfact fixture evidence; currentfact fixture evidence",
                    }
                ),
            )
            reference = ArtifactRef(family="topic-memory", artifact_id=artifact.artifact_id, revision=artifact.revision)
            await lifecycle.register_topic(
                "mixedsummary",
                reference,
                parents=("oldfact", "currentfact"),
                policy="all",
            )
            bindings["mixedsummary"] = (("oldfact", "currentfact"), "all", "topic")
            citations["mixedsummary"] = reference
            await lifecycle.apply(event("root0", 1, False))
            await assert_runtime(runtime, scope, bindings, citations, oracle(bindings, {"root0"}))
            assert await lifecycle.resolve("currentfact", purpose="generation") == citations["currentfact"]
            assert await lifecycle.resolve("mixedsummary", purpose="audit") == reference

    asyncio.run(run())
