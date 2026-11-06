from __future__ import annotations

import asyncio
import math

from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage
from powercontext.builtin.inference.coalescing import CoalescingEmbeddingModel
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts


class MeteredEmbedding:
    profile = EmbeddingProfile(profile_id="request-budget-v1", model="offline", dimension=24)

    def __init__(self):
        self.calls = []

    async def embed(self, texts):
        self.calls.append(tuple(texts))
        vectors = tuple(tuple(float(f"needle{index} fixture" == value) for index in range(24)) for value in texts)
        return EmbeddingResult(
            vectors=vectors, usage=InferenceUsage(requests=1, input_tokens=len(texts) * 7, output_tokens=0)
        )


def test_concurrent_real_memory_writes_share_bounded_provider_work_and_cached_search(tmp_path):
    async def run():
        base = MeteredEmbedding()
        model = CoalescingEmbeddingModel(base, max_batch_size=6, coalesce_ms=0, cache_size=48)
        parties = 6

        class StartTogether:
            profile = model.profile

            def __init__(self):
                self.arrived = 0
                self.release = asyncio.Event()

            async def embed(self, texts):
                if self.arrived < parties:
                    self.arrived += 1
                    if self.arrived == parties:
                        self.release.set()
                    await self.release.wait()
                return await model.embed(texts)

        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'batching.db'}"))
        async with open_builtin_contexts(config, embedding_model=StartTogether()) as contexts:
            services = [(await contexts.get(f"scope-{index}")).artifacts.memory for index in range(parties)]
            texts = tuple(f"needle{index} fixture" for index in range(24))
            entries = tuple(MemoryEntryInput(kind="fact", text=value) for value in texts)
            memories = await asyncio.wait_for(
                asyncio.gather(
                    *(service.remember(memory=None, entries=entries, mode="append") for service in services)
                ),
                timeout=30,
            )
            assert len(base.calls) == math.ceil(len(texts) / 6)
            assert all(1 <= len(call) <= 6 for call in base.calls)
            assert sorted(value for call in base.calls for value in call) == sorted(texts)
            for index, (service, memory) in enumerate(zip(services, memories, strict=True)):
                result = await service.search(texts[index], memories=(memory,), limit=1, mode="vector")
                assert result.hits[0].text == texts[index]
            assert len(base.calls) == 4, "cache hits must not issue provider requests"

    asyncio.run(run())
