"""Count admission through public APIs and real native workers."""

import asyncio
import sqlite3

import pytest
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    CaptureSource,
    HandoffContent,
    HandoffSourceCitation,
    HandoffStatement,
    PreparedHandoff,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.builtin.work import AcknowledgeHandoff, HandoffReceipt, content_digest
from pydantic import ValidationError

from .checks import (
    capture,
    check_reads,
    config,
    messages,
    provider,
    receipt_refs,
    scope,
    source_refs,
    stay_refs,
    wait_refs,
)


def test_strict_optional_positive_thresholds():
    for field in ("memory_source_count_threshold", "topic_memory_source_count_threshold"):
        assert field in RuntimeConfig.model_fields, f"Missing public configuration: {field}"
        assert getattr(RuntimeConfig(), field) is None
        for value in (None, 1, 3, 100):
            assert getattr(RuntimeConfig(**{field: value}), field) == value
        for value in (True, False, 0, -1, 1.0, "3"):
            with pytest.raises(ValidationError):
                RuntimeConfig(**{field: value})


async def test_memory_threshold_duplicate_and_single_window(tmp_path, monkeypatch):
    with provider(monkeypatch) as server:
        settings = config(tmp_path / "memory.db", server, memory_source_count_threshold=3, source_window_limit=1)
        async with open_builtin_runtime(settings) as runtime:
            sid = await scope(runtime)
            rows = messages()
            receipts = await capture(runtime, sid, rows[:2])
            replay = await capture(runtime, sid, [rows[0], rows[1], rows[0]])
            assert [r.sequence for r in replay] == [receipts[0].sequence, receipts[1].sequence, receipts[0].sequence]
            await stay_refs(runtime, sid, "memory", set())
            receipts += await capture(runtime, sid, rows[2:3])
            first = receipt_refs(receipts[:1])
            await wait_refs(runtime, sid, "memory", first)
            await stay_refs(runtime, sid, "memory", first)
            assert (await runtime.memory.for_scope(sid).cursor()).sequence == receipts[0].sequence
            receipts += await capture(runtime, sid, rows[3:4])
            second = receipt_refs(receipts[:2])
            await wait_refs(runtime, sid, "memory", second)
            await stay_refs(runtime, sid, "memory", second)
            await check_reads(runtime, sid, "memory", second)
        async with open_builtin_runtime(settings) as runtime:
            await stay_refs(runtime, sid, "memory", second)
            await check_reads(runtime, sid, "memory", second)
            await runtime.memory.for_scope(sid).flush()
            third = receipt_refs(receipts[:3])
            await wait_refs(runtime, sid, "memory", third)
            await stay_refs(runtime, sid, "memory", third)


async def test_topic_native_wave_and_restart_below_threshold(tmp_path, monkeypatch):
    with provider(monkeypatch) as server:
        settings = config(
            tmp_path / "topic.db",
            server,
            families=("topic-memory",),
            topic_memory_source_count_threshold=3,
            topic_memory_source_window_limit=1,
        )
        async with open_builtin_runtime(settings) as runtime:
            sid = await scope(runtime)
            receipts = await capture(runtime, sid, messages()[:2])
            await capture(runtime, sid, messages()[:2])
            await stay_refs(runtime, sid, "topic-memory", set())
        async with open_builtin_runtime(settings) as runtime:
            await stay_refs(runtime, sid, "topic-memory", set())
            receipts += await capture(runtime, sid, messages()[2:3])
            expected = receipt_refs(receipts)
            await wait_refs(runtime, sid, "topic-memory", expected)
            await stay_refs(runtime, sid, "topic-memory", expected)
            await check_reads(runtime, sid, "topic-memory", expected)
        async with open_builtin_runtime(settings) as runtime:
            await check_reads(runtime, sid, "topic-memory", expected)
            await capture(runtime, sid, messages()[3:4])
            await stay_refs(runtime, sid, "topic-memory", expected)
            await runtime.topic_memory.for_scope(sid).flush()
            final = expected | {("content", messages()[3].source_id)}
            await wait_refs(runtime, sid, "topic-memory", final)
            await check_reads(runtime, sid, "topic-memory", final)


async def test_scope_and_family_thresholds_are_independent(tmp_path, monkeypatch):
    with provider(monkeypatch) as server:
        settings = config(
            tmp_path / "independent.db",
            server,
            families=("memory", "topic-memory"),
            memory_source_count_threshold=2,
            topic_memory_source_count_threshold=3,
        )
        async with open_builtin_runtime(settings) as runtime:
            left, right = await scope(runtime, "left"), await scope(runtime, "right")
            a = await capture(runtime, left, messages()[:1])
            b = await capture(runtime, right, messages()[:1])
            for sid in (left, right):
                await stay_refs(runtime, sid, "memory", set())
                await stay_refs(runtime, sid, "topic-memory", set())
            a += await capture(runtime, left, messages()[1:2])
            await wait_refs(runtime, left, "memory", receipt_refs(a))
            await stay_refs(runtime, left, "topic-memory", set())
            await stay_refs(runtime, right, "memory", set())
            b += await capture(runtime, right, messages()[1:2])
            await wait_refs(runtime, right, "memory", receipt_refs(b))
            await stay_refs(runtime, right, "topic-memory", set())
            memory_expected = receipt_refs(b)
            b += await capture(runtime, right, messages()[2:3])
            await wait_refs(runtime, right, "topic-memory", receipt_refs(b))
            await stay_refs(runtime, right, "memory", memory_expected)
            await check_reads(runtime, left, "memory", receipt_refs(a))
            await check_reads(runtime, right, "topic-memory", receipt_refs(b))


@pytest.mark.parametrize("family", ["memory", "topic-memory"])
@pytest.mark.parametrize("threshold", [None, 5])
async def test_time_condition_is_or_not_and(tmp_path, monkeypatch, family, threshold):
    with provider(monkeypatch) as server:
        prefix = "memory" if family == "memory" else "topic_memory"
        settings = config(
            tmp_path / "time.db",
            server,
            families=(family,),
            **{f"{prefix}_source_count_threshold": threshold, f"{prefix}_schedule_seconds": 1.0},
        )
        async with open_builtin_runtime(settings) as runtime:
            sid = await scope(runtime)
            expected = receipt_refs(await capture(runtime, sid, messages()[:1]))
            await wait_refs(runtime, sid, family, expected)
            await check_reads(runtime, sid, family, expected)


@pytest.mark.parametrize("family", ["memory", "topic-memory"])
async def test_disabled_threshold_preserves_explicit_flush(tmp_path, monkeypatch, family):
    with provider(monkeypatch) as server:
        settings = config(tmp_path / "disabled.db", server, families=(family,))
        async with open_builtin_runtime(settings) as runtime:
            sid = await scope(runtime)
            expected = receipt_refs(await capture(runtime, sid, messages()[:3]))
            await stay_refs(runtime, sid, family, set())
            app = runtime.memory if family == "memory" else runtime.topic_memory
            await app.for_scope(sid).flush()
            await wait_refs(runtime, sid, family, expected)
            await check_reads(runtime, sid, family, expected)


async def test_native_receipt_trust_upgrade_does_not_add_a_pending_identity(tmp_path, monkeypatch):
    with provider(monkeypatch) as server:
        settings = config(tmp_path / "receipt.db", server, memory_source_count_threshold=4)
        async with open_builtin_runtime(settings) as runtime:
            sid = await scope(runtime)
            receipts = await capture(runtime, sid, messages()[:1])
            prepared = PreparedHandoff(
                scope_id=sid,
                base=None,
                content=HandoffContent(
                    objective="Confirm the sampled conversation processing boundary",
                    state=(
                        HandoffStatement(
                            text="The captured source is available for counting.",
                            citations=(HandoffSourceCitation(source_ref=receipts[0].source_ref),),
                        ),
                    ),
                    disposition="continuable",
                ),
            )
            receipt = HandoffReceipt(
                receiver="count-fixture",
                status="needs_clarification",
                selection="prepared",
                prepared_digest=content_digest(prepared),
                evidence_status="available",
                message="Count fixture acknowledgement.",
            )
            command = CaptureSource(
                source_id="count-receipt",
                content=receipt.model_dump_json(by_alias=True, exclude_none=False, indent=2),
                metadata={"kind": "handoff-receipt", "schema": receipt.model_dump(by_alias=True)["schema"]},
            )
            receipts += await capture(runtime, sid, [command])
            records = runtime.records.for_scope(sid)
            before = await records.get_source("content", command.source_id)
            assert before.handoff_receipt is False
            acknowledged = await runtime.work.for_scope(sid).acknowledge(
                AcknowledgeHandoff(
                    source_id=command.source_id,
                    receiver=receipt.receiver,
                    status=receipt.status,
                    selection="prepared",
                    prepared=prepared,
                    message=receipt.message,
                )
            )
            after = await records.get_source("content", command.source_id)
            assert after.handoff_receipt is True
            assert after.content == before.content
            assert acknowledged.receipt.position == before.position == after.position == receipts[1].sequence
            receipts += await capture(runtime, sid, messages()[1:2])
            await stay_refs(runtime, sid, "memory", set())
            receipts += await capture(runtime, sid, messages()[2:3])
            expected = receipt_refs(receipts)
            await wait_refs(runtime, sid, "memory", expected)
            await check_reads(runtime, sid, "memory", expected)


@pytest.mark.parametrize("family", ["memory", "topic-memory"])
@pytest.mark.parametrize("last_write", ["ordinary_capture_last", "lineage_last"])
async def test_lineage_only_journal_identity_counts_without_becoming_generation_evidence(
    tmp_path, monkeypatch, family, last_write
):
    with provider(monkeypatch) as server:
        prefix = "memory" if family == "memory" else "topic_memory"
        settings = config(
            tmp_path / "lineage.db", server, families=(family,), **{f"{prefix}_source_count_threshold": 3}
        )
        async with open_builtin_runtime(settings) as runtime:
            sid = await scope(runtime)
            receipts = await capture(runtime, sid, messages()[:1])
            if last_write == "lineage_last":
                receipts += await capture(runtime, sid, messages()[1:2])
                await stay_refs(runtime, sid, family, set())
            content = HandoffContent(
                objective="Continue the sampled conversation",
                state=(
                    HandoffStatement(
                        text="The sampled source is available.",
                        citations=(HandoffSourceCitation(source_ref=receipts[0].source_ref),),
                    ),
                ),
                disposition="continuable",
            )
            created = await runtime.records.for_scope(sid).create_artifact(
                "handoff", ArtifactWrite(content=content.model_dump(mode="json", by_alias=True))
            )
            assert created.family == "handoff"
            if last_write == "ordinary_capture_last":
                await stay_refs(runtime, sid, family, set())
                receipts += await capture(runtime, sid, messages()[1:2])
            expected = receipt_refs(receipts)
            await wait_refs(runtime, sid, family, expected)
            await check_reads(runtime, sid, family, expected)


@pytest.mark.parametrize("family", ["memory", "topic-memory"])
async def test_storage_failure_retries_after_restart_without_new_input(tmp_path, monkeypatch, family):
    database = tmp_path / "failure.db"
    with provider(monkeypatch) as server:
        prefix = "memory" if family == "memory" else "topic_memory"
        settings = config(database, server, families=(family,), **{f"{prefix}_source_count_threshold": 2})
        async with open_builtin_runtime(settings) as runtime:
            sid = await scope(runtime)
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "CREATE TRIGGER interrupt_output BEFORE INSERT ON pc_artifacts "
                    f"WHEN NEW.family = '{family}' BEGIN SELECT RAISE(ABORT, 'controlled processing failure'); END"
                )
            expected = receipt_refs(await capture(runtime, sid, messages()[:2]))
            deadline = asyncio.get_running_loop().time() + 30
            while not server.generation_calls and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.1)
            assert server.generation_calls > 0, "No native generation was attempted after reaching the threshold"
            await stay_refs(runtime, sid, family, set())
            assert await source_refs(runtime, sid, family) == set()
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TRIGGER interrupt_output")
        async with open_builtin_runtime(settings) as runtime:
            await wait_refs(runtime, sid, family, expected)
            await check_reads(runtime, sid, family, expected)
            await stay_refs(runtime, sid, family, expected)
