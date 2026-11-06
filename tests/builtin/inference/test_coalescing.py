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

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage
from powercontext.builtin.inference.coalescing import CoalescingEmbeddingModel


class Embedding:
    profile = EmbeddingProfile(profile_id="coalescing-tests", model="local", dimension=1)

    def __init__(self):
        self.calls = []

    async def embed(self, texts):
        self.calls.append(texts)
        return EmbeddingResult(
            vectors=tuple((float(len(text)),) for text in texts),
            usage=InferenceUsage(requests=2, input_tokens=11, output_tokens=3),
        )


def test_usage_counts_each_shared_batch_once_per_caller() -> None:
    async def scenario():
        base = Embedding()
        model = CoalescingEmbeddingModel(base, max_batch_size=2, coalesce_ms=0)
        first, second = await asyncio.gather(model.embed(("one", "two", "one", "three")), model.embed(("two", "three")))
        assert first.vectors == ((3.0,), (3.0,), (3.0,), (5.0,))
        assert second.vectors == ((3.0,), (5.0,))
        assert first.usage == second.usage == InferenceUsage(requests=4, input_tokens=22, output_tokens=6)
        assert base.calls == [("one", "two"), ("three",)]
        assert (await model.embed(("one", "three"))).usage.requests == 0

    asyncio.run(scenario())


def test_late_waiter_shares_running_work_even_when_cache_is_disabled() -> None:
    class Blocked(Embedding):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def embed(self, texts):
            self.calls.append(texts)
            self.started.set()
            await self.release.wait()
            return EmbeddingResult(vectors=tuple((1.0,) for _ in texts), usage=InferenceUsage(requests=1))

    async def scenario():
        base = Blocked()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0, cache_size=0)
        first = asyncio.create_task(model.embed(("shared",)))
        await base.started.wait()
        late = asyncio.create_task(model.embed(("shared",)))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        base.release.set()
        assert (await late).vectors == ((1.0,),)
        assert base.calls == [("shared",)]
        await model.embed(("shared",))
        assert base.calls == [("shared",), ("shared",)]

    asyncio.run(scenario())


def test_one_failed_batch_does_not_discard_independent_success() -> None:
    class Partial(Embedding):
        async def embed(self, texts):
            if texts == ("bad",):
                self.calls.append(texts)
                raise RuntimeError("controlled provider failure")  # noqa: TRY003
            return await super().embed(texts)

    async def scenario():
        base = Partial()
        model = CoalescingEmbeddingModel(base, max_batch_size=1, coalesce_ms=0)
        with pytest.raises(RuntimeError):
            await model.embed(("bad", "good"))
        assert (await model.embed(("good",))).usage.requests == 0
        with pytest.raises(RuntimeError):
            await model.embed(("bad",))
        assert base.calls.count(("bad",)) == 2 and base.calls.count(("good",)) == 1

    asyncio.run(scenario())


def test_lru_access_order_includes_duplicate_positions() -> None:
    async def scenario():
        base = Embedding()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0, cache_size=2)
        await model.embed(("a", "b", "a"))
        await model.embed(("c",))
        assert (await model.embed(("a",))).usage.requests == 0
        assert (await model.embed(("b",))).usage.requests == 2

    asyncio.run(scenario())


def test_asymmetric_query_operation_has_its_own_cache_namespace() -> None:
    class Asymmetric(Embedding):
        async def embed_query(self, texts):
            self.calls.append(("QUERY", *texts))
            return EmbeddingResult(vectors=tuple((-float(len(text)),) for text in texts))

    async def scenario():
        base = Asymmetric()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0)
        document, query = await asyncio.gather(model.embed(("same",)), model.embed_query(("same",)))
        assert document.vectors == ((4.0,),) and query.vectors == ((-4.0,),)
        assert (await model.embed_query(("same",))).usage.requests == 0
        assert len(base.calls) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "settings",
    [
        {"max_batch_size": True},
        {"max_batch_size": 0},
        {"max_batch_size": 1.5},
        {"cache_size": -1},
        {"cache_size": True},
        {"coalesce_ms": -1},
        {"coalesce_ms": float("nan")},
        {"coalesce_ms": float("inf")},
    ],
)
def test_invalid_settings_are_rejected(settings) -> None:
    with pytest.raises(ValueError):
        CoalescingEmbeddingModel(Embedding(), **settings)
