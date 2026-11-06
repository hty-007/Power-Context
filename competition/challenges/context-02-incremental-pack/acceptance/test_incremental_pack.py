from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, PrepareContextRequest, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource


class _CandidatePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="conversation-turn", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


def test_wire_payload_and_digest_match_an_independent_oracle() -> None:
    from powercontext.builtin.runtime.context_pack import ContextPack, ContextPackDelta, ContextPackError

    items = (b"alpha\n", "\u6d77\u6d0b".encode())
    unsigned = {
        "schema": "powercontext.context-pack.v1",
        "items": [base64.b64encode(item).decode("ascii") for item in items],
    }
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    pack = ContextPack.build(items)
    payload = json.loads(pack.to_bytes())
    assert payload == {
        **unsigned,
        "digest": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        "byte_count": len(pack.to_bytes()),
    }
    assert pack.to_bytes() == json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    delta = ContextPackDelta.between(pack, ContextPack.build((*items, b"tail")))
    delta_payload = json.loads(delta.to_bytes())
    assert set(delta_payload) == {"schema", "base_digest", "target_digest", "prefix_items", "appended"}
    assert delta_payload["schema"] == "powercontext.context-pack-delta.v1"
    assert delta_payload["base_digest"] == pack.digest
    assert delta_payload["target_digest"] == ContextPack.build((*items, b"tail")).digest
    prefix = delta_payload["prefix_items"]
    assert type(prefix) is int and 0 <= prefix <= len(items)
    assert items[:prefix] + tuple(base64.b64decode(value) for value in delta_payload["appended"]) == (*items, b"tail")
    for field in payload:
        incomplete = {key: value for key, value in payload.items() if key != field}
        with pytest.raises(ContextPackError):
            ContextPack.from_bytes(json.dumps(incomplete).encode())
    with pytest.raises(ContextPackError):
        ContextPack.from_content("\u6d77", chunk_bytes=2)


def test_context_pack_round_trip_is_public() -> None:
    from powercontext.builtin.runtime.context_pack import ContextPack, ContextPackDelta, apply_context_pack_delta

    base = ContextPack.build((b"alpha", b"beta"))
    target = ContextPack.build((b"alpha", b"beta", b"gamma"))
    delta = ContextPackDelta.between(base, target)
    assert apply_context_pack_delta(base, delta).to_bytes() == target.to_bytes()


def test_delta_rejects_wrong_base() -> None:
    import pytest
    from powercontext.builtin.runtime.context_pack import (
        ContextPack,
        ContextPackDelta,
        ContextPackError,
        apply_context_pack_delta,
    )

    base = ContextPack.build((b"one",))
    delta = ContextPackDelta.between(base, ContextPack.build((b"one", b"two")))
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(ContextPack.build((b"other",)), delta)


def test_utf8_chunks_reconstruct_content_and_report_exact_bytes() -> None:
    from powercontext.builtin.runtime.context_pack import ContextPack

    pack = ContextPack.from_content("alpha \u6d77\u6d0b beta", chunk_bytes=7)
    assert b"".join(pack.items).decode("utf-8") == "alpha \u6d77\u6d0b beta"
    assert pack.byte_count == len(pack.to_bytes())


@pytest.mark.parametrize(
    "target_items", [(b"a", b"b", b"new", b"tail"), (b"new", b"b", b"old"), (b"a", b"new", b"old"), ()]
)
def test_delta_reconstructs_changes_at_any_position(target_items) -> None:
    from powercontext.builtin.runtime.context_pack import ContextPack, ContextPackDelta, apply_context_pack_delta

    base = ContextPack.build((b"a", b"b", b"old"))
    target = ContextPack.build(target_items)
    delta = ContextPackDelta.between(base, target)
    rebuilt = apply_context_pack_delta(
        ContextPack.from_bytes(base.to_bytes()), ContextPackDelta.from_bytes(delta.to_bytes())
    )
    assert rebuilt.to_bytes() == target.to_bytes()
    assert delta.byte_count == len(delta.to_bytes())
    full_suffix = {
        **json.loads(delta.to_bytes()),
        "prefix_items": 0,
        "appended": [base64.b64encode(item).decode("ascii") for item in target_items],
    }
    decoded = ContextPackDelta.from_bytes(json.dumps(full_suffix).encode())
    assert apply_context_pack_delta(base, decoded).to_bytes() == target.to_bytes()


def test_wire_decoders_reject_tampering() -> None:
    from powercontext.builtin.runtime.context_pack import ContextPack, ContextPackDelta, ContextPackError

    pack = ContextPack.build((b"alpha",))
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(pack.to_bytes().replace(pack.digest.encode(), b"sha256:" + b"0" * 64))
    delta = ContextPackDelta.between(pack, ContextPack.build((b"alpha", b"beta")))
    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(json.dumps({**json.loads(delta.to_bytes()), "prefix_items": -1}).encode())


@pytest.mark.parametrize("prefix", [True, 1.5, -1])
def test_delta_rejects_non_integer_or_negative_prefix(prefix: object) -> None:
    from powercontext.builtin.runtime.context_pack import ContextPack, ContextPackDelta, ContextPackError

    base = ContextPack.build((b"alpha",))
    delta = ContextPackDelta.between(base, ContextPack.build((b"alpha", b"beta")))
    payload = json.loads(delta.to_bytes())
    payload["prefix_items"] = prefix
    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def test_empty_pack_round_trip_and_delta_bounds() -> None:
    from powercontext.builtin.runtime.context_pack import (
        ContextPack,
        ContextPackDelta,
        ContextPackError,
        apply_context_pack_delta,
    )

    empty = ContextPack.build(())
    assert ContextPack.from_bytes(empty.to_bytes()) == empty
    invalid = ContextPackDelta(empty.digest, "sha256:" + "0" * 64, 1, ())
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(empty, invalid)
    wrong_target = ContextPackDelta(empty.digest, "sha256:" + "0" * 64, 0, (b"x",))
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(empty, wrong_target)


@pytest.mark.asyncio
async def test_runtime_prepare_incremental_replays_after_serialization() -> None:
    from powercontext.builtin.runtime.context_pack import ContextPack, ContextPackDelta, apply_context_pack_delta

    sample = json.loads((Path(__file__).parents[1] / "workload/public/turns.json").read_text(encoding="utf-8"))
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=_CandidatePipeline()) as runtime:
        scope = await runtime.scopes.create(
            ScopeDraft(
                title="Incremental pack",
                summary="Runtime integration",
                idempotency_key="context-02-incremental-pack-runtime",
            )
        )
        sources = runtime.sources.for_scope(scope.scope_id)
        memory = runtime.memory.for_scope(scope.scope_id)
        context = runtime.context.for_scope(scope.scope_id)
        await sources.capture(CaptureSource(source_id="turn-1", content=sample["turns"][0]["text"], metadata={}))
        while (await memory.flush()).processed:
            pass
        first = await context.prepare_incremental(PrepareContextRequest(query=sample["query"], max_bytes=4096))
        assert isinstance(first, ContextPack)
        first_content = b"".join(first.items).decode("utf-8")
        await sources.capture(CaptureSource(source_id="turn-2", content=sample["turns"][1]["text"], metadata={}))
        while (await memory.flush()).processed:
            pass
        second = await context.prepare_incremental(
            PrepareContextRequest(query=sample["query"], max_bytes=4096), base_pack=first
        )
        assert isinstance(second, ContextPackDelta)
        expected = await context.prepare(PrepareContextRequest(query=sample["query"], max_bytes=4096))

    restored_base = ContextPack.from_bytes(first.to_bytes())
    restored_delta = ContextPackDelta.from_bytes(second.to_bytes())
    rebuilt = apply_context_pack_delta(restored_base, restored_delta)
    assert b"".join(rebuilt.items).decode("utf-8") == expected.content
    assert first_content
    assert expected.content and expected.content != first_content
    assert len(second.to_bytes()) == second.byte_count
