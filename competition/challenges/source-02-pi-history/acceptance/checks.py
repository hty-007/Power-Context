from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

from powercontext.builtin.runtime import open_builtin_runtime
from powercontext.sources import SourceDefinitionRegistry, project_source_for_transport
from powercontext.sources.pi_session import PI_EVENT_SOURCE_DEFINITION, PiSessionInput, parse_pi_session
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY

store = importlib.import_module("challenges.source-02-pi-history.acceptance.evidence_store")


async def check_sessions(workload: Path, output: Path) -> None:
    cases = json.loads((workload / "cases.json").read_text())["cases"]
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    observations, queries = ([], [])
    identities = set()
    for case in cases:
        raw = (workload / case["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == case["sha256"]
        records = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
        by_id = {record["id"]: record for record in records[1:]}
        events = parse_pi_session(PiSessionInput(content=raw, leaf_id=case["leaf_id"]))
        assert isinstance(events, tuple)
        for event in events:
            expected = event.model_dump()
            for key, value in expected.items():
                assert getattr(event, key) == value
            assert event.record == by_id[event.event_id], "retain the complete input record"
            source = await registry.resolve(event)
            assert source.name not in identities, "distinct session events must have distinct identities"
            identities.add(source.name)
            assert await registry.read(source) == event
            assert (await registry.resolve(event)).name == source.name
            observation = project_source_for_transport(registry, source)
            evidence = observation.projection(TEXT_EVIDENCE_PROJECTION_KEY)
            assert evidence["source_type"] == "pi-event"
            assert evidence["source_id"] == source.name
            assert evidence["content"] == expected["content"]
            for key in ("session_id", "event_id", "parent_id", "event_type", "role"):
                assert evidence["metadata"][key] == expected[key]
            observations.append(observation)
        queries.extend((event.role, event.role) for event in events)
    assert len({source.name for source in observations}) == len(observations)
    config, scope_id, stored = await store.persist_events(output, PI_EVENT_SOURCE_DEFINITION, observations)
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope_id)
        retrieved = set()
        for _, query in set(queries):
            retrieved.update(source.name for source in await store.search_events(memory, query, stored))
        assert retrieved == set(stored), "every visible event must retain a retrievable exact citation"
