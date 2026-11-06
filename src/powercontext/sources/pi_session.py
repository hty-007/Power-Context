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

"""Capture original Pi branch history independently of model context compaction."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, JsonValue, TypeAdapter

from powercontext.errors import SourceConflictError
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.definitions import SourceDefinition, SourceProjection
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

_RECORD = TypeAdapter(dict[str, JsonValue])


class PiSessionInput(BaseModel):
    """An offline Pi v3 JSONL archive and an optional selected tree node."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One original visible event, including its complete unmodified record."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """A captured Pi event whose evidence survives restarts and branch changes."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def _required_string(value: JsonValue, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Pi {field} must be a non-empty string")  # noqa: TRY003
    return value


def _string(value: JsonValue, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Pi {field} must be a string")  # noqa: TRY003, TRY004
    return value


def _canonical_json(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _event_identity(session_id: str, event_id: str) -> str:
    # Separate JSON components avoid delimiter collisions and bound long provider IDs.
    components = _canonical_json([session_id, event_id]).encode("utf-8")
    return f"pi:{hashlib.sha256(components).hexdigest()}"


def _record_digest(record: dict[str, JsonValue]) -> str:
    return hashlib.sha256(_canonical_json(record).encode("utf-8")).hexdigest()


def _visible_text(value: JsonValue, *, tool_calls: bool = False) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if not isinstance(value, list):
        raise ValueError("Pi content must be a string or an array of content blocks")  # noqa: TRY003, TRY004
    parts: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            raise ValueError("Pi content blocks must be objects")  # noqa: TRY003, TRY004
        if block.get("type") == "text":
            parts.append(_string(block.get("text"), "text block"))
        elif tool_calls and block.get("type") == "toolCall":
            name = _required_string(block.get("name"), "tool name")
            call_id = _required_string(block.get("id"), "tool call ID")
            arguments = _canonical_json(block.get("arguments", {}))
            parts.append(f"[tool_call {name} {call_id}] {arguments}")
    return "\n".join(parts)


def _visible_event(
    session_id: str, event_id: str, parent_id: str | None, record: dict[str, JsonValue]
) -> PiEventInput | None:
    event_type = _required_string(record.get("type"), "event type")
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            raise ValueError("Pi message events must contain a message object")  # noqa: TRY003
        role = _required_string(message.get("role"), "message role")
        if role == "bashExecution":
            command = _string(message.get("command", ""), "bash command")
            output = _string(message.get("output", ""), "bash output")
            body = f"$ {command}\n{output}"
        else:
            body = _visible_text(message.get("content"), tool_calls=True)
            if role == "toolResult":
                name = _required_string(message.get("toolName"), "tool name")
                call_id = _required_string(message.get("toolCallId"), "tool result call ID")
                body = f"[tool_result {name} {call_id}] {body}"
    elif event_type in {"compaction", "branch_summary"}:
        role = event_type
        body = _string(record.get("summary", ""), "summary")
    elif event_type == "custom_message":
        role = "custom"
        body = _visible_text(record.get("content"))
    else:
        return None
    if not body:
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=event_id,
        parent_id=parent_id,
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-JSON number in Pi archive: {value}")  # noqa: TRY003


@dataclass(frozen=True)
class _PiArchive:
    session_id: str
    records: dict[str, dict[str, JsonValue]]
    events: tuple[PiEventInput, ...]


def _read_records(content: bytes) -> tuple[str, list[dict[str, JsonValue]]]:
    records: list[dict[str, JsonValue]] = []
    for line in content.decode("utf-8-sig", errors="strict").split("\n"):
        if not line.strip():
            continue
        record = json.loads(line, parse_constant=_reject_json_constant)
        if not isinstance(record, dict):
            raise ValueError("Pi JSONL records must be objects")  # noqa: TRY003, TRY004
        records.append(_RECORD.validate_python(record))
    if not records:
        raise ValueError("Pi archive must contain a session header")  # noqa: TRY003
    header = records[0]
    if header.get("type") != "session" or type(header.get("version")) is not int or header.get("version") != 3:
        raise ValueError("Pi archive must start with a version 3 session header")  # noqa: TRY003
    session_id = _required_string(header.get("id"), "session ID")
    return session_id, records[1:]


def _parse_archive(value: PiSessionInput) -> _PiArchive:
    session_id, records = _read_records(value.content)
    by_id: dict[str, dict[str, JsonValue]] = {}
    parents: dict[str, str | None] = {}
    for record in records:
        event_id = _required_string(record.get("id"), "event ID")
        _required_string(record.get("type"), "event type")
        if event_id in by_id:
            raise ValueError("Pi event IDs must be unique")  # noqa: TRY003
        if "parentId" not in record:
            raise ValueError("Pi event must declare parentId")  # noqa: TRY003
        parent = record["parentId"]
        if parent is not None and (not isinstance(parent, str) or parent not in by_id):
            raise ValueError("Pi parentId must be null or reference an earlier node")  # noqa: TRY003
        by_id[event_id] = record
        parents[event_id] = parent
    leaf = value.leaf_id if value.leaf_id is not None else next(reversed(by_id), None)
    if leaf is not None and leaf not in by_id:
        raise ValueError("Selected Pi leaf does not exist")  # noqa: TRY003
    events: list[PiEventInput] = []
    while leaf is not None:
        event = _visible_event(session_id, leaf, parents[leaf], by_id[leaf])
        if event is not None:
            events.append(event)
        leaf = parents[leaf]
    return _PiArchive(session_id=session_id, records=by_id, events=tuple(reversed(events)))


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Return every visible original event along the selected root-to-leaf path."""
    return _parse_archive(value).events


class _PiEventTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class: type[PiEventSource] = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pi-event",
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


class _PiEventSourceDefinition:
    name = "pi-event"
    version = "1"
    input_class: type[PiEventInput] = PiEventInput
    source_class: type[PiEventSource] = PiEventSource
    projections: tuple[SourceProjection[PiEventSource], ...] = (_PiEventTextEvidenceProjection(),)

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        _required_string(value.session_id, "session ID")
        _required_string(value.event_id, "event ID")
        return PiEventSource(
            name=_event_identity(value.session_id, value.event_id),
            materialization=SourceMaterialization.CAPTURED,
            **value.model_dump(),
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput.model_validate(source.model_dump())


PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = _PiEventSourceDefinition()


class _PiCheckpoint(BaseModel):
    version: Literal[1] = 1
    sessions: dict[str, dict[str, str]] = Field(default_factory=dict)


class PiSessionConnector:
    """Submit unseen branch events, committing progress only through the lifecycle."""

    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        content = await asyncio.to_thread(self.path.read_bytes)
        archive = _parse_archive(PiSessionInput(content=content, leaf_id=self.leaf_id))
        state = _PiCheckpoint() if session.checkpoint is None else _PiCheckpoint.model_validate(session.checkpoint)
        imported = dict(state.sessions.get(archive.session_id, {}))
        # Validate all previously captured events, including events on unselected branches,
        # before making any durable submissions. JSON formatting and key order are immaterial.
        for event_id, digest in imported.items():
            record = archive.records.get(event_id)
            if record is None or _record_digest(record) != digest:
                raise ValueError("A previously imported Pi event was changed or removed")  # noqa: TRY003
        for event in archive.events:
            if event.event_id in imported:
                continue
            try:
                outcome = await session.submit(_event_identity(event.session_id, event.event_id), "pi-event", event)
            except SourceConflictError as error:
                # A failed earlier run can have durable events without a committed checkpoint.
                raise ValueError("A previously captured Pi event conflicts with the input") from error  # noqa: TRY003
            if outcome.status is not ConnectorSubmissionStatus.ACCEPTED:
                return ConnectorRunCompletion(status=ConnectorRunStatus.INCOMPLETE, checkpoint=session.checkpoint)
            imported[event.event_id] = _record_digest(event.record)
        state.sessions[archive.session_id] = imported
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=state.model_dump(mode="json"))


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
]
