"""Exercise source connectors against persistent native Runtime boundaries."""

from __future__ import annotations

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CommitConnectorCheckpoint,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport
from powercontext.sources.connectors import ConnectorLifecycle


class RuntimeSink:
    def __init__(self, runtime, registry):
        self.runtime, self.registry = runtime, registry

    async def submit(self, binding, item_id, definition_name, value):
        source = await self.registry.resolve(value)
        return (
            await self.runtime.ingestion.submit(
                SubmitSourceObservation(
                    scope_id=binding.scope_id, observation=project_source_for_transport(self.registry, source)
                )
            )
        ).source_ref


class RuntimeCheckpoints:
    def __init__(self, runtime):
        self.runtime = runtime

    async def load(self, binding):
        return (await self.runtime.ingestion.checkpoint(binding)).checkpoint

    async def save(self, binding, checkpoint, *, expected):
        await self.runtime.ingestion.commit(
            CommitConnectorCheckpoint(binding=binding, checkpoint=checkpoint, expected=expected)
        )


async def run_connector_persisted(tmp_path, definition, connector, binding, *, database_name="connector.db"):
    registry = SourceDefinitionRegistry((definition,))
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / database_name}"))
    async with open_builtin_runtime(config) as runtime:
        scope = await runtime.scopes.create(
            ScopeDraft(title="Source", summary="Source", idempotency_key=binding.scope_id)
        )
        binding = binding.model_copy(update={"scope_id": scope.scope_id})
        await runtime.ingestion.register(manifest_for_definition(definition))
        result = await ConnectorLifecycle(
            sink=RuntimeSink(runtime, registry), checkpoints=RuntimeCheckpoints(runtime)
        ).run(connector, binding)
    async with open_builtin_contexts(config) as contexts:
        entries = await (await contexts.get(binding.scope_id)).sources.journal.entries()
    return result, entries
