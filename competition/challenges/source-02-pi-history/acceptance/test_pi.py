from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from powercontext.builtin.runtime import SubmitSourceObservation, open_builtin_runtime
from powercontext.errors import SourceConflictError
from powercontext.sources import SourceDefinitionRegistry, project_source_for_transport
from powercontext.sources.pi_session import PI_EVENT_SOURCE_DEFINITION, PiSessionInput, parse_pi_session
from selftest_support.source_acceptance import persist, search_sources

checks = importlib.import_module("challenges.source-02-pi-history.acceptance.checks")


async def test_public_sessions(tmp_path: Path) -> None:
    await checks.check_sessions(Path(__file__).parents[1] / "workload/public", tmp_path)


def session(records, leaf=None):
    header = {"type": "session", "version": 3, "id": "control-session"}
    raw = "\n".join(json.dumps(record) for record in [header, *records]).encode()
    return parse_pi_session(PiSessionInput(content=raw, leaf_id=leaf))


async def test_explicit_branch_preserves_tool_and_summary_events(tmp_path) -> None:
    records = [
        {"type": "message", "id": "a", "parentId": None, "message": {"role": "user", "content": "alpha"}},
        {"type": "message", "id": "b", "parentId": "a", "message": {"role": "assistant", "content": "other"}},
        {"type": "compaction", "id": "c", "parentId": "a", "summary": "retained summary"},
        {"type": "branch_summary", "id": "d", "parentId": "c", "summary": "branch summary"},
        {
            "type": "message",
            "id": "e",
            "parentId": "d",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "private reasoning"},
                    {"type": "toolCall", "name": "read", "id": "call-1", "arguments": {"path": "a.txt"}},
                ],
            },
        },
        {
            "type": "message",
            "id": "f",
            "parentId": "e",
            "message": {
                "role": "toolResult",
                "toolName": "read",
                "toolCallId": "call-1",
                "content": [{"type": "text", "text": "file contents"}],
            },
        },
    ]
    events = session(records, "f")
    assert [event.event_id for event in events] == ["a", "c", "d", "e", "f"]
    assert [event.content for event in events] == [
        "user: alpha",
        "compaction: retained summary",
        "branch_summary: branch summary",
        'assistant: [tool_call read call-1] {"path":"a.txt"}',
        "toolResult: [tool_result read call-1] file contents",
    ]
    assert session(records, "b")[1].content == "assistant: other"
    assert session(records) == events
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    assert (await registry.resolve(session(records, "b")[0])).name == (await registry.resolve(events[0])).name
    observations = [project_source_for_transport(registry, await registry.resolve(event)) for event in events]
    config, scope_id, stored = await persist(tmp_path, PI_EVENT_SOURCE_DEFINITION, observations)
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope_id)
        for event, observation in zip(events, observations, strict=True):
            found = await search_sources(memory, event.role, stored, limit=20)
            assert observation.name in {source.name for source in found}


async def test_reimport_rejects_changed_event_payload(tmp_path: Path) -> None:
    records = [
        {"type": "message", "id": "root", "parentId": None, "message": {"role": "user", "content": "first version"}}
    ]
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    first = project_source_for_transport(registry, await registry.resolve(session(records)[0]))
    config, scope_id, _ = await persist(tmp_path, PI_EVENT_SOURCE_DEFINITION, [first])
    records[0]["message"]["content"] = "conflicting version"
    changed = project_source_for_transport(registry, await registry.resolve(session(records)[0]))
    assert changed.name == first.name
    async with open_builtin_runtime(config) as runtime:
        with pytest.raises(SourceConflictError):
            await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope_id, observation=changed))


@pytest.mark.parametrize(
    "records,leaf",
    [
        ([{"type": "model_change", "id": "a", "parentId": "missing"}], "a"),
        ([{"type": "model_change", "id": "a", "parentId": "a"}], "a"),
        ([{"type": "model_change", "id": "a", "parentId": None}] * 2, "a"),
        ([{"type": "model_change", "id": "a", "parentId": None}], "absent"),
    ],
)
def test_invalid_graphs_fail_atomically(records, leaf) -> None:
    with pytest.raises(ValueError):
        session(records, leaf)


@pytest.mark.parametrize(
    "raw", [b"", b"{}\n", b"[]\n", b"null\n", b"\xff", b'{"type":"session","version":2,"id":"x"}\n']
)
def test_invalid_archives_fail(raw) -> None:
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=raw))


def test_bom_bash_custom_and_control_parent_are_preserved():
    header = {"type": "session", "version": 3, "id": "formats"}
    records = [
        {"type": "message", "id": "a", "parentId": None, "message": {"role": "user", "content": "Request"}},
        {"type": "thinking_level_change", "id": "b", "parentId": "a"},
        {
            "type": "message",
            "id": "c",
            "parentId": "b",
            "message": {"role": "bashExecution", "command": "printf result", "output": "result", "content": []},
        },
        {
            "type": "custom_message",
            "id": "d",
            "parentId": "c",
            "content": [{"type": "text", "text": "Custom evidence"}],
        },
        {
            "type": "message",
            "id": "e",
            "parentId": "d",
            "message": {
                "role": "assistant",
                "content": [{"type": "image", "data": "ignored"}, {"type": "text", "text": "Answer"}],
            },
        },
    ]
    raw = b"\xef\xbb\xbf" + "\n".join(json.dumps(row) for row in [header, *records]).encode()
    events = parse_pi_session(PiSessionInput(content=raw))
    assert [event.content for event in events] == [
        "user: Request",
        "bashExecution: $ printf result\nresult",
        "custom: Custom evidence",
        "assistant: Answer",
    ]
    assert events[1].parent_id == "b" and [event.event_id for event in events] == ["a", "c", "d", "e"]
