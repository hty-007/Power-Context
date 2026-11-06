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

import pytest

from powercontext.builtin.runtime import BuiltinConfig, PrepareContextRequest, open_builtin_runtime
from powercontext.builtin.runtime.context_pack import (
    ContextPack,
    ContextPackDelta,
    ContextPackError,
    apply_context_pack_delta,
)


@pytest.mark.parametrize("size", [0, 1, 50, 500, 5000])
def test_self_inclusive_wire_lengths_and_separate_receiver(size: int) -> None:
    base = ContextPack.from_content("海洋🙂\n" * size, chunk_bytes=37)
    assert base.byte_count == len(base.to_bytes())
    assert ContextPack.from_bytes(base.to_bytes()) == base
    target = ContextPack.from_content("海洋🙂\n" * size + "new suffix", chunk_bytes=37)
    delta = ContextPackDelta.from_bytes(ContextPackDelta.between(base, target).to_bytes())
    restored = apply_context_pack_delta(ContextPack.from_bytes(base.to_bytes()), delta)
    assert restored == target
    assert delta.byte_count == len(delta.to_bytes())
    assert all(len(chunk) <= 37 and chunk.decode() for chunk in base.items)


@pytest.mark.parametrize("limit", [True, 0, -1, 1.5, "512"])
def test_chunk_limits_are_strict_integers(limit) -> None:
    with pytest.raises(ContextPackError):
        ContextPack.from_content("context", chunk_bytes=limit)


@pytest.mark.parametrize("raw", [b"null", b"[]", b"{", b"\xff", b'{"schema":"x","schema":"x"}'])
def test_invalid_wire_shapes_fail_with_protocol_errors(raw: bytes) -> None:
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(raw)
    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(raw)


@pytest.mark.parametrize("item", ["%%%", "YQ", "Zh==", "/w==", 1, None])
def test_base64_and_chunk_encoding_are_validated(item) -> None:
    base = ContextPack.build(())
    delta = json.loads(ContextPackDelta.between(base, base).to_bytes())
    delta["appended"] = [item]
    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(json.dumps(delta).encode())


def test_empty_native_prepare_is_a_legal_empty_pack_and_delta() -> None:
    async def scenario():
        async with open_builtin_runtime(BuiltinConfig()) as runtime:
            from powercontext.builtin.scope import ScopeDraft

            scope = await runtime.scopes.create(
                ScopeDraft(title="Empty", summary="Empty", idempotency_key="empty-pack")
            )
            context = runtime.context.for_scope(scope.scope_id)
            request = PrepareContextRequest(query="no evidence yet")
            base = await context.prepare_incremental(request)
            assert isinstance(base, ContextPack) and base.items == ()
            delta = await context.prepare_incremental(request, base_pack=base)
            restored = apply_context_pack_delta(
                ContextPack.from_bytes(base.to_bytes()), ContextPackDelta.from_bytes(delta.to_bytes())
            )
            assert b"".join(restored.items) == b""

    asyncio.run(scenario())
