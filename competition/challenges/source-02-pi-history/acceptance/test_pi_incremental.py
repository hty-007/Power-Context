import json

import pytest
from powercontext.sources import ConnectorBinding
from powercontext.sources.pi_session import PI_EVENT_SOURCE_DEFINITION, PiSessionConnector
from selftest_support.runtime_fixture import run_connector_persisted


def write(path, records):
    header = {"type": "session", "version": 3, "id": "tree"}
    path.write_text("\n".join(json.dumps(row) for row in [header, *records]) + "\n", encoding="utf-8")


async def test_branch_switch_restart_full_evidence_and_mutation_rejection(tmp_path):
    path = tmp_path / "tree.jsonl"
    records = [
        {"type": "message", "id": "root", "parentId": None, "message": {"role": "user", "content": "Shared request"}},
        {
            "type": "message",
            "id": "left",
            "parentId": "root",
            "message": {"role": "assistant", "content": "Left evidence"},
        },
        {"type": "model_change", "id": "control", "parentId": "root"},
        {
            "type": "message",
            "id": "right",
            "parentId": "control",
            "message": {"role": "assistant", "content": "Right evidence"},
        },
    ]
    write(path, records)
    binding = ConnectorBinding(
        scope_id="tree", binding_id="pi", connector_name="pi-session-history", connector_version="1"
    )

    async def run(leaf):
        return await run_connector_persisted(
            tmp_path, PI_EVENT_SOURCE_DEFINITION, PiSessionConnector(path, leaf), binding
        )

    first, entries = await run("left")
    assert [row.source.projections[0].value["content"] for row in entries] == [
        "user: Shared request",
        "assistant: Left evidence",
    ]
    second, entries = await run("right")
    assert len(second.items) == 1
    assert second.previous_checkpoint == first.committed_checkpoint
    assert [row.source.projections[0].value["content"] for row in entries] == [
        "user: Shared request",
        "assistant: Left evidence",
        "assistant: Right evidence",
    ]
    replay, persisted = await run("right")
    assert replay.items == () and persisted == entries
    records.append(
        {
            "type": "message",
            "id": "tail",
            "parentId": "right",
            "message": {"role": "assistant", "content": "Later evidence"},
        }
    )
    write(path, records)
    added, entries = await run("tail")
    assert len(added.items) == 1 and len(entries) == 4
    path.write_text(path.read_text() + "{bad json}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        await run("tail")
    write(path, records)
    repaired, _ = await run("tail")
    assert repaired.previous_checkpoint == added.committed_checkpoint and repaired.items == ()
    records[0]["message"]["content"] = "Changed request"
    write(path, records)
    with pytest.raises(ValueError):
        await run("tail")
    records[0]["message"]["content"] = "Shared request"
    write(path, records)
    stable, _ = await run("tail")
    assert stable.previous_checkpoint == added.committed_checkpoint
