from __future__ import annotations

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    CaptureSource,
    ContextAssembly,
    ContextAssemblySection,
    GetMemoryEntryRequest,
    PrepareContextRequest,
    RememberMemoryRequest,
    SearchMemoryRequest,
    SearchTopicMemoryRequest,
)
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleEvent
from powercontext.builtin.scope import ScopeDraft


async def build(runtime, *, suffix="public", count=8):
    scope = await runtime.scopes.create(
        ScopeDraft(title="Lifecycle", summary="Dependency evidence", idempotency_key=suffix)
    )
    lifecycle = runtime.lifecycle.for_scope(scope.scope_id)
    roots = {}
    for number in range(4):
        node = f"root{number}"
        receipt = await runtime.sources.for_scope(scope.scope_id).capture(
            CaptureSource(source_id=node, content=f"Origin {node}", metadata={})
        )
        roots[node] = receipt.source_ref
        await lifecycle.register_source(node, receipt.source_ref, revision=3)
    bindings = {}
    citations = dict(roots)
    for number in range(count):
        node = f"needle{number}"
        parents = (f"root{number % 4}", f"root{(number + 1) % 4}") if number < 4 else (f"needle{number - 4}",)
        policy = "any" if number % 2 else "all"
        result = await runtime.memory.for_scope(scope.scope_id).remember(
            RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text=f"{node} fixture evidence"),))
        )
        await lifecycle.register_memory(node, result.entry.citation, parents=parents, policy=policy)
        bindings[node] = (parents, policy, "memory")
        citations[node] = result.entry.citation
    for number in range(4):
        node = f"topicneedle{number}"
        created = await runtime.records.for_scope(scope.scope_id).create_artifact(
            "topic-memory",
            ArtifactWrite(
                content={"title": node, "summary": f"{node} supported summary", "detail": f"{node} detailed evidence"}
            ),
        )
        reference = ArtifactRef(family="topic-memory", artifact_id=created.artifact_id, revision=created.revision)
        parents = (f"needle{number}", f"needle{number + 4}")
        await lifecycle.register_topic(node, reference, parents=parents, policy="all")
        bindings[node] = (parents, "all", "topic")
        citations[node] = reference
    return scope.scope_id, lifecycle, bindings, citations


def oracle(bindings, withdrawn):
    active = {f"root{number}" for number in range(4)} - withdrawn
    for node, (parents, policy, _) in bindings.items():
        support = [parent in active for parent in parents]
        if all(support) if policy == "all" else any(support):
            active.add(node)
    return active


async def assert_runtime(runtime, scope, bindings, citations, active):
    memory = runtime.memory.for_scope(scope)
    entries = await memory.list(include_inactive=True)
    states = {record.entry.text.split()[0]: record.state for record in entries.entries}
    for node, (_, _, family) in bindings.items():
        if family == "memory":
            assert (states[node] == "active") == (node in active)
            page = await memory.search(SearchMemoryRequest(query=node, mode="fts", limit=10))
            assert any(hit.text == f"{node} fixture evidence" for hit in page.hits) == (node in active)
            original = await memory.get(GetMemoryEntryRequest(citation=citations[node]))
            assert original.entry.text == f"{node} fixture evidence"
        else:
            page = await runtime.topic_memory.for_scope(scope).search(SearchTopicMemoryRequest(query=node, limit=20))
            assert bool(page.hits) == (node in active)
            reference = citations[node]
            record = await runtime.records.for_scope(scope).get_artifact_revision(
                "topic-memory", reference.artifact_id, reference.revision
            )
            assert record.content["title"] == node
        prepared = await runtime.context.for_scope(scope).prepare(
            PrepareContextRequest(
                query=node,
                max_bytes=4096,
                assembly=ContextAssembly(
                    sections=(
                        ContextAssemblySection(family="memory", limit=6),
                        ContextAssemblySection(family="topic-memory", limit=2),
                    )
                ),
            )
        )
        expected_text = f"{node} fixture evidence" if family == "memory" else f"{node} supported summary"
        assert (expected_text in (prepared.content or "")) == (node in active)


def event(root, sequence, available):
    return SourceLifecycleEvent(
        node_id=root, revision=3, sequence=sequence, available=available, reason="controlled lifecycle"
    )
