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
from copy import deepcopy

import pytest

from powercontext.sources import (
    ConnectorBinding,
    SourceDefinitionRegistry,
    SourceMaterialization,
    SourceRef,
    project_source_for_transport,
)
from powercontext.sources.connectors import ConnectorLifecycle
from powercontext.sources.pi_session import (
    PI_EVENT_SOURCE_DEFINITION,
    PiSessionConnector,
    PiSessionInput,
    parse_pi_session,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


def archive(records, session_id="session") -> bytes:
    return "\n".join(
        json.dumps(record, ensure_ascii=False)
        for record in [{"type": "session", "version": 3, "id": session_id}, *records]
    ).encode()


def message(event_id, parent=None, text="original"):
    return {"type": "message", "id": event_id, "parentId": parent, "message": {"role": "user", "content": text}}


def test_tool_arguments_and_unicode_text_retain_their_exact_content() -> None:
    record = message("tool")
    record["message"] = {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "omitted from projection"},
            {"type": "text", "text": "前\u2028后"},
            {"type": "toolCall", "name": "read", "id": "调用", "arguments": {"z": 2, "a": "中文"}},
            {"type": "image", "data": "still in original record"},
        ],
    }
    event = parse_pi_session(PiSessionInput(content=archive([record])))[0]
    assert event.content == 'assistant: 前\u2028后\n[tool_call read 调用] {"a":"中文","z":2}'
    assert event.record == record


@pytest.mark.parametrize(
    "records",
    [
        [message("root"), message("unselected", "absent")],
        [message("root"), {"type": "label", "id": "unselected"}],
        [message("root"), message("future", "later"), message("later")],
        [message("root"), message("cycle", "cycle")],
        [message("root"), {"type": "label", "id": "root", "parentId": None}],
    ],
)
def test_invalid_unselected_branches_are_rejected_before_returning_events(records) -> None:
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=archive(records), leaf_id="root"))


def test_header_only_and_non_finite_json() -> None:
    assert parse_pi_session(PiSessionInput(content=archive([]))) == ()
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=archive([]), leaf_id="missing"))
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=archive([message("root")]) + b'\n{"bad":NaN}'))


def test_source_keeps_large_tool_results_and_identity_is_bounded() -> None:
    text = "原始工具结果\n" * 5000
    record = {
        "type": "message",
        "id": "event:长" * 100,
        "parentId": None,
        "message": {"role": "toolResult", "toolName": "read", "toolCallId": "call", "content": text},
    }
    event = parse_pi_session(PiSessionInput(content=archive([record], "session:长" * 100)))[0]
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    source = asyncio.run(registry.resolve(event))
    assert source.materialization is SourceMaterialization.CAPTURED
    assert len(source.name) <= 256
    assert asyncio.run(registry.read(source)) == event
    observation = project_source_for_transport(registry, source)
    assert (
        observation.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"] == f"toolResult: [tool_result read call] {text}"
    )


def test_failed_sink_does_not_commit_progress_and_restarts_replay_safely(tmp_path) -> None:
    class Sink:
        def __init__(self):
            self.values = {}
            self.fail = True

        async def submit(self, binding, item_id, definition_name, value):
            del binding
            if value.event_id == "tail" and self.fail:
                raise OSError("injected sink failure")  # noqa: TRY003
            self.values[item_id] = value
            return SourceRef(source_type=definition_name, source_id=item_id)

    class Checkpoints:
        value = None

        async def load(self, binding):
            return deepcopy(self.value)

        async def save(self, binding, checkpoint, *, expected):
            assert self.value == expected
            self.value = deepcopy(checkpoint)

    async def scenario():
        path = tmp_path / "session.jsonl"
        path.write_bytes(archive([message("root"), message("tail", "root")]))
        sink, checkpoints = Sink(), Checkpoints()
        lifecycle = ConnectorLifecycle(sink=sink, checkpoints=checkpoints)
        binding = ConnectorBinding(
            scope_id="scope", binding_id="pi", connector_name="pi-session-history", connector_version="1"
        )
        with pytest.raises(OSError):
            await lifecycle.run(PiSessionConnector(path), binding)
        assert checkpoints.value is None and len(sink.values) == 1
        sink.fail = False
        result = await lifecycle.run(PiSessionConnector(path), binding)
        assert len(result.items) == 2 and len(sink.values) == 2
        replay = await lifecycle.run(PiSessionConnector(path), binding)
        assert replay.items == () and replay.committed_checkpoint == result.committed_checkpoint
        path.write_bytes(archive([message("root", text="changed"), message("tail", "root"), message("new", "tail")]))
        with pytest.raises(ValueError):
            await lifecycle.run(PiSessionConnector(path), binding)
        assert checkpoints.value == result.committed_checkpoint and len(sink.values) == 2
        records = [message("root"), message("tail", "root"), message("side", "root")]
        path.write_bytes(archive(records))
        switched = await lifecycle.run(PiSessionConnector(path, "side"), binding)
        assert len(switched.items) == 1 and len(sink.values) == 3
        records[1]["timestamp"] = "changed metadata on the unselected branch"
        path.write_bytes(archive(records))
        with pytest.raises(ValueError):
            await lifecycle.run(PiSessionConnector(path, "side"), binding)
        assert checkpoints.value == switched.committed_checkpoint and len(sink.values) == 3

    asyncio.run(scenario())
