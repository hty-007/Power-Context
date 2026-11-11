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

"""Offline acceptance tests for cross-call embedding coalescing and vector caching."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference.coalescing import CoalescingEmbeddingModel
from powercontext.builtin.inference.models import EmbeddingResult, InferenceUsage

DIMENSION = 4


def _vector(text: str, dimension: int = DIMENSION) -> tuple[float, ...]:
    seed = sum(text.encode("utf-8"))
    return tuple(float((seed + index) % 97) for index in range(dimension))


class OfflineEmbeddingModel:
    """Deterministic provider that records the exact batches it receives."""

    def __init__(self, dimension: int = DIMENSION) -> None:
        self.profile = EmbeddingProfile(profile_id="offline", model="offline", dimension=dimension)
        self.batches: list[tuple[str, ...]] = []
        self.fail_prefix: str | None = None
        self.hold: Callable[[tuple[str, ...]], bool] | None = None
        self.entered: asyncio.Event | None = None
        self.release: asyncio.Event | None = None
        self.short_result = False

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        self.batches.append(tuple(texts))
        if self.hold is not None and self.hold(texts):
            if self.entered is not None:
                self.entered.set()
            if self.release is not None:
                await self.release.wait()
        if self.fail_prefix is not None and any(text.startswith(self.fail_prefix) for text in texts):
            raise RuntimeError("provider failure")
        vectors = tuple(_vector(text, self.profile.dimension) for text in texts)
        if self.short_result:
            vectors = vectors[:-1]
        return EmbeddingResult(
            vectors=vectors,
            usage=InferenceUsage(requests=1, input_tokens=sum(len(text) for text in texts)),
        )

    def sent(self) -> list[str]:
        return [text for batch in self.batches for text in batch]


class OfflineQueryEmbeddingModel(OfflineEmbeddingModel):
    """Asymmetric provider whose query vectors occupy a different space than documents."""

    def __init__(self, dimension: int = DIMENSION) -> None:
        super().__init__(dimension)
        self.query_batches: list[tuple[str, ...]] = []

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        self.query_batches.append(tuple(texts))
        return EmbeddingResult(
            vectors=tuple(tuple(-value for value in _vector(text, self.profile.dimension)) for text in texts),
            usage=InferenceUsage(requests=1, input_tokens=sum(len(text) for text in texts)),
        )


def _wrapper(model: OfflineEmbeddingModel, **overrides: int) -> CoalescingEmbeddingModel:
    options: dict[str, float] = {"max_batch_size": 64, "coalesce_ms": 0.0, "cache_size": 1024}
    options.update(overrides)  # type: ignore[arg-type]
    return CoalescingEmbeddingModel(model, **options)  # type: ignore[arg-type]


def test_duplicate_texts_share_one_batch_and_keep_positions() -> None:
    model = OfflineEmbeddingModel()
    wrapper = _wrapper(model)

    result = asyncio.run(wrapper.embed(("alpha", "beta", "alpha")))

    assert len(result.vectors) == 3
    assert result.vectors[0] == result.vectors[2]
    assert result.vectors[0] != result.vectors[1]
    assert model.sent() == ["alpha", "beta"]
    assert result.usage.requests == 1


def test_concurrent_calls_share_one_underlying_batch() -> None:
    model = OfflineEmbeddingModel()
    wrapper = _wrapper(model)

    async def run() -> list[EmbeddingResult]:
        return await asyncio.gather(*(wrapper.embed(("alpha", "beta")) for _ in range(4)))

    results = asyncio.run(run())

    assert len(model.batches) == 1
    assert results[0].vectors == results[3].vectors
    for result in results:
        assert result.usage.requests == 1


def test_late_caller_joins_the_unfinished_batch() -> None:
    model = OfflineEmbeddingModel()
    wrapper = _wrapper(model)

    async def run() -> tuple[EmbeddingResult, EmbeddingResult]:
        model.hold = lambda _texts: True
        model.entered = asyncio.Event()
        model.release = asyncio.Event()
        first = asyncio.ensure_future(wrapper.embed(("late",)))
        await model.entered.wait()
        second = asyncio.ensure_future(wrapper.embed(("late",)))
        await asyncio.sleep(0)
        model.release.set()
        return await asyncio.gather(first, second)

    first, second = asyncio.run(run())

    assert len(model.batches) == 1
    assert first.vectors == second.vectors


def test_asymmetric_provider_receives_each_kind_on_its_own_method() -> None:
    model = OfflineQueryEmbeddingModel()
    wrapper = _wrapper(model)

    async def run() -> None:
        await wrapper.embed(("document-only",))
        await wrapper.embed_query(("query-only",))

    asyncio.run(run())

    assert model.batches == [("document-only",)]
    assert model.query_batches == [("query-only",)]


def test_same_text_is_shared_across_document_and_query_calls() -> None:
    model = OfflineQueryEmbeddingModel()
    wrapper = _wrapper(model)

    async def run() -> tuple[EmbeddingResult, EmbeddingResult]:
        return await wrapper.embed(("shared",)), await wrapper.embed_query(("shared",))

    document, query = asyncio.run(run())

    assert model.batches == [("shared",)]
    assert model.query_batches == []
    assert document.vectors == query.vectors
    assert query.usage.requests == 0


def test_concurrent_documents_and_queries_never_share_a_batch() -> None:
    model = OfflineQueryEmbeddingModel()
    wrapper = _wrapper(model, max_batch_size=8)

    async def run() -> None:
        await asyncio.gather(
            wrapper.embed(("doc-a", "doc-b")),
            wrapper.embed_query(("query-a", "query-b")),
        )

    asyncio.run(run())

    assert model.batches == [("doc-a", "doc-b")]
    assert model.query_batches == [("query-a", "query-b")]


def test_empty_input_never_reaches_the_provider() -> None:
    model = OfflineEmbeddingModel()
    wrapper = _wrapper(model)

    result = asyncio.run(wrapper.embed(()))

    assert model.batches == []
    assert result.vectors == ()
    assert result.usage.requests == 0


def test_batch_never_exceeds_the_limit_and_sends_each_text_once() -> None:
    model = OfflineEmbeddingModel()
    wrapper = _wrapper(model, max_batch_size=6)
    texts = tuple(f"text-{index}" for index in range(24))

    result = asyncio.run(wrapper.embed(texts))

    assert len(result.vectors) == 24
    assert model.sent() == list(texts)
    assert len(model.batches) == 4
    assert all(len(batch) <= 6 for batch in model.batches)


def test_lru_keeps_recently_used_entries() -> None:
    model = OfflineEmbeddingModel()
    wrapper = _wrapper(model, cache_size=2)

    async def run() -> None:
        await wrapper.embed(("alpha",))
        await wrapper.embed(("beta",))
        await wrapper.embed(("alpha",))  # refreshes alpha, so beta becomes the eviction candidate
        await wrapper.embed(("gamma",))
        await wrapper.embed(("beta",))

    asyncio.run(run())

    assert model.sent() == ["alpha", "beta", "gamma", "beta"]
    assert len(model.batches) == 4


def test_cache_hit_survives_eviction_during_the_same_call() -> None:
    """A caller that read a warm entry must not need it back after waiting for other batches."""

    model = OfflineEmbeddingModel()
    wrapper = _wrapper(model, cache_size=1)

    async def run() -> EmbeddingResult:
        await wrapper.embed(("cached",))
        model.hold = lambda texts: "fresh" in texts
        model.entered = asyncio.Event()
        model.release = asyncio.Event()
        running = asyncio.ensure_future(wrapper.embed(("cached", "fresh")))
        await model.entered.wait()
        await wrapper.embed(("churn",))  # fills the size-1 cache and evicts "cached"
        model.release.set()
        return await running

    result = asyncio.run(run())

    assert len(result.vectors) == 2
    assert model.sent() == ["cached", "fresh", "churn"]


def test_cache_size_zero_disables_reuse_but_keeps_coalescing() -> None:
    model = OfflineEmbeddingModel()
    wrapper = _wrapper(model, cache_size=0)

    async def run() -> None:
        await asyncio.gather(wrapper.embed(("same",)), wrapper.embed(("same",)))
        await wrapper.embed(("same",))

    asyncio.run(run())

    # One shared concurrent batch, and one fresh request because nothing was cached.
    assert len(model.batches) == 2


def test_failure_is_not_cached_and_can_be_retried() -> None:
    model = OfflineEmbeddingModel()
    wrapper = _wrapper(model, max_batch_size=1)
    model.fail_prefix = "boom"

    with pytest.raises(RuntimeError):
        asyncio.run(wrapper.embed(("boom-text",)))

    model.fail_prefix = None
    result = asyncio.run(wrapper.embed(("boom-text",)))

    assert len(result.vectors) == 1
    assert len(model.batches) == 2
    assert asyncio.run(wrapper.embed(("boom-text",))).usage.requests == 0


def test_partial_failure_leaves_independent_batches_intact() -> None:
    model = OfflineEmbeddingModel()
    wrapper = _wrapper(model, max_batch_size=1)
    model.fail_prefix = "bad"

    async def run() -> list[object]:
        return await asyncio.gather(
            wrapper.embed(("good-text",)),
            wrapper.embed(("bad-text",)),
            return_exceptions=True,
        )

    outcomes = asyncio.run(run())

    assert not isinstance(outcomes[0], BaseException)
    assert isinstance(outcomes[1], RuntimeError)
    # The successful batch was cached, so a later call costs nothing.
    assert asyncio.run(wrapper.embed(("good-text",))).usage.requests == 0


def test_cancelling_one_caller_keeps_the_shared_batch_alive() -> None:
    model = OfflineEmbeddingModel()
    wrapper = _wrapper(model)

    async def run() -> EmbeddingResult:
        model.hold = lambda _texts: True
        model.entered = asyncio.Event()
        model.release = asyncio.Event()
        survivor = asyncio.ensure_future(wrapper.embed(("shared",)))
        await model.entered.wait()
        doomed = asyncio.ensure_future(wrapper.embed(("shared",)))
        await asyncio.sleep(0)
        doomed.cancel()
        model.release.set()
        with pytest.raises(asyncio.CancelledError):
            await doomed
        return await survivor

    result = asyncio.run(run())

    assert len(model.batches) == 1
    assert len(result.vectors) == 1


def test_usage_counts_each_waited_batch_once() -> None:
    model = OfflineEmbeddingModel()
    wrapper = _wrapper(model, max_batch_size=6)
    texts = tuple(f"text-{index}" for index in range(12))

    result = asyncio.run(wrapper.embed(texts))

    assert result.usage.requests == 2
    assert result.usage.input_tokens == sum(len(text) for text in texts)

    async def shared() -> list[EmbeddingResult]:
        return await asyncio.gather(*(wrapper.embed(("joined",)) for _ in range(3)))

    for single in asyncio.run(shared()):
        assert single.usage.requests == 1


def test_cached_call_reports_zero_requests() -> None:
    model = OfflineEmbeddingModel()
    wrapper = _wrapper(model)

    async def run() -> EmbeddingResult:
        await wrapper.embed(("warm",))
        return await wrapper.embed(("warm",))

    result = asyncio.run(run())

    assert len(model.batches) == 1
    assert result.usage.requests == 0


def test_vector_count_mismatch_raises_value_error() -> None:
    model = OfflineEmbeddingModel()
    model.short_result = True
    wrapper = _wrapper(model)

    with pytest.raises(ValueError):
        asyncio.run(wrapper.embed(("first", "second")))


def test_rejects_invalid_configuration() -> None:
    model = OfflineEmbeddingModel()

    with pytest.raises(ValueError):
        CoalescingEmbeddingModel(model, max_batch_size=0)
    with pytest.raises(ValueError):
        CoalescingEmbeddingModel(model, max_batch_size=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        CoalescingEmbeddingModel(model, cache_size=-1)
    with pytest.raises(ValueError):
        CoalescingEmbeddingModel(model, coalesce_ms=-1.0)


def test_native_request_budget_six_scopes_and_cache_queries() -> None:
    model = OfflineEmbeddingModel()
    wrapper = _wrapper(model, max_batch_size=6, cache_size=48)
    texts = tuple(f"session-{index}" for index in range(24))

    async def run() -> list[EmbeddingResult]:
        scopes = await asyncio.gather(*(wrapper.embed(texts) for _ in range(6)))
        queries = [await wrapper.embed_query(texts) for _ in range(6)]
        return scopes + queries

    results = asyncio.run(run())

    assert len(model.batches) == 4
    assert sorted(model.sent()) == sorted(texts)
    assert all(len(batch) <= 6 for batch in model.batches)
    for result in results[6:]:
        assert result.usage.requests == 0
