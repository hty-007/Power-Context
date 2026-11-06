from __future__ import annotations

import asyncio

import pytest
from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage
from powercontext.builtin.inference.coalescing import CoalescingEmbeddingModel
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts


class Embedding:
    profile = EmbeddingProfile(profile_id="memory-02-embedding-batches", model="local", dimension=1)

    def __init__(self) -> None:
        self.calls = []

    async def embed(self, texts):
        self.calls.append(texts)
        return EmbeddingResult(vectors=tuple((float(len(text)),) for text in texts), usage=InferenceUsage(requests=1))


def test_deduplicates_concurrent_calls_and_restores_order() -> None:
    async def scenario() -> None:
        base = Embedding()
        model = CoalescingEmbeddingModel(base, max_batch_size=2, coalesce_ms=20, cache_size=3)
        first, second = await asyncio.gather(model.embed(("alpha", "beta", "alpha")), model.embed(("beta", "gamma")))
        assert first.vectors == ((5.0,), (4.0,), (5.0,))
        assert second.vectors == ((4.0,), (5.0,))
        assert sorted(text for call in base.calls for text in call) == ["alpha", "beta", "gamma"]
        assert all(len(call) <= 2 for call in base.calls)
        assert first.usage.requests == sum(bool(set(call) & {"alpha", "beta"}) for call in base.calls)
        assert second.usage.requests == sum(bool(set(call) & {"beta", "gamma"}) for call in base.calls)
        await model.embed(("alpha",))
        assert sum(call.count("alpha") for call in base.calls) == 1

    asyncio.run(scenario())


def test_lru_eviction_and_invalid_vector_count() -> None:
    class Invalid(Embedding):
        async def embed(self, texts):
            return EmbeddingResult(vectors=())

    async def scenario() -> None:
        base = Embedding()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0, cache_size=1)
        await model.embed(("one",))
        await model.embed(("two",))
        await model.embed(("one",))
        assert [call for call in base.calls] == [("one",), ("two",), ("one",)]
        with pytest.raises(ValueError):
            await CoalescingEmbeddingModel(Invalid(), coalesce_ms=0).embed(("broken",))

    asyncio.run(scenario())


def test_empty_input_and_recent_cache_access_preserve_request_budget() -> None:
    async def scenario() -> None:
        base = Embedding()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0, cache_size=2)
        empty = await model.embed(())
        assert empty.vectors == ()
        assert empty.usage.requests == 0
        assert base.calls == []
        await model.embed(("alpha",))
        await model.embed(("beta",))
        await model.embed(("alpha",))
        await model.embed(("gamma",))
        await model.embed(("alpha",))
        assert sum(call.count("alpha") for call in base.calls) == 1
        await model.embed(("beta",))
        assert sum(call.count("beta") for call in base.calls) == 2

    asyncio.run(scenario())


def test_cancelling_one_waiter_does_not_cancel_shared_embedding() -> None:
    class Blocked(Embedding):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def embed(self, texts):
            self.calls.append(texts)
            self.started.set()
            await self.release.wait()
            return EmbeddingResult(vectors=((1.0,),))

    async def scenario() -> None:
        base = Blocked()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0)
        cancelled = asyncio.create_task(model.embed(("shared",)))
        survivor = asyncio.create_task(model.embed(("shared",)))
        await base.started.wait()
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        base.release.set()
        assert (await survivor).vectors == ((1.0,),)
        assert base.calls == [("shared",)]

    asyncio.run(scenario())


def test_failure_is_not_cached() -> None:
    class Flaky(Embedding):
        async def embed(self, texts):
            self.calls.append(texts)
            if len(self.calls) == 1:
                raise RuntimeError("temporary")
            return EmbeddingResult(vectors=((1.0,),))

    async def scenario() -> None:
        model = CoalescingEmbeddingModel(Flaky(), coalesce_ms=0)
        with pytest.raises(RuntimeError):
            await model.embed(("retry",))
        assert (await model.embed(("retry",))).vectors == ((1.0,),)

    asyncio.run(scenario())


def test_provider_usage_is_preserved_and_cache_hits_do_not_charge_again() -> None:
    class Metered(Embedding):
        async def embed(self, texts):
            self.calls.append(texts)
            return EmbeddingResult(
                vectors=tuple((1.0,) for _ in texts),
                usage=InferenceUsage(requests=1, input_tokens=7, output_tokens=0),
            )

    async def scenario() -> None:
        model = CoalescingEmbeddingModel(Metered(), coalesce_ms=0)
        result = await model.embed(("first", "first"))
        assert result.usage.input_tokens == 7
        assert result.usage.output_tokens == 0
        cached = await model.embed(("first",))
        assert cached.usage.requests == 0
        assert cached.usage.input_tokens in (None, 0)

    asyncio.run(scenario())


def test_wrapper_drives_native_memory_vector_index() -> None:
    class SemanticEmbedding:
        profile = EmbeddingProfile(profile_id="memory-02-embedding-batches-native", model="local", dimension=2)

        async def embed(self, texts):
            return EmbeddingResult(
                vectors=tuple((1.0, 0.0) if "alpha" in text.lower() else (0.0, 1.0) for text in texts)
            )

    async def scenario() -> None:
        model = CoalescingEmbeddingModel(SemanticEmbedding(), coalesce_ms=0)
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig()), embedding_model=model) as contexts:
            service = (await contexts.get("memory-02-embedding-batches-native")).artifacts.memory
            memory = await service.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="fact", text="Alpha launch decision."),
                    MemoryEntryInput(kind="fact", text="Beta operational note."),
                ),
                mode="append",
            )
            assert memory is not None
            result = await service.search("alpha", memories=(memory,), limit=1, mode="vector")
            assert result.hits[0].text == "Alpha launch decision."

    asyncio.run(scenario())
