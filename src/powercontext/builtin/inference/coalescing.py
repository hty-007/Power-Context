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

"""Event-loop-local shared embedding work, bounded batches, and successful-vector LRU."""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict, deque
from dataclasses import dataclass

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel, QueryEmbeddingModel, embed_query

_Key = tuple[bool, str]


@dataclass(frozen=True, slots=True)
class _Embedded:
    vector: EmbeddingVector
    batch_id: int | None = None
    usage: InferenceUsage | None = None


def _observe_failure(future: asyncio.Future[_Embedded]) -> None:
    # All callers can cancel while the provider is still running. Retrieve its eventual
    # error even then; this does not prevent surviving/future awaiters from seeing it.
    if not future.cancelled():
        future.exception()


def _combined_usage(results: dict[str, _Embedded]) -> InferenceUsage:
    batches = {result.batch_id: result.usage for result in results.values() if result.usage is not None}
    inputs = [usage.input_tokens for usage in batches.values() if usage.input_tokens is not None]
    outputs = [usage.output_tokens for usage in batches.values() if usage.output_tokens is not None]
    return InferenceUsage(
        requests=sum(usage.requests for usage in batches.values()),
        input_tokens=sum(inputs) if inputs else None,
        output_tokens=sum(outputs) if outputs else None,
    )


class CoalescingEmbeddingModel:
    """Coordinate one provider's requests on a single event loop without owning callers."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")  # noqa: TRY003
        if (
            isinstance(coalesce_ms, bool)
            or not isinstance(coalesce_ms, (int, float))
            or not 0 <= coalesce_ms < math.inf
        ):
            raise ValueError("coalesce_ms must be finite and non-negative")  # noqa: TRY003
        self.profile = model.profile
        self._model = model
        self._max_batch_size = max_batch_size
        self._delay = coalesce_ms / 1000
        self._cache_size = cache_size
        self._cache: OrderedDict[_Key, EmbeddingVector] = OrderedDict()
        self._inflight: dict[_Key, asyncio.Future[_Embedded]] = {}
        self._pending: tuple[deque[_Key], deque[_Key]] = (deque(), deque())
        self._dispatch_task: asyncio.Task[None] | None = None
        self._workers: set[asyncio.Task[None]] = set()
        self._batch_id = 0

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return await self._embed(texts, query=False)

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        # Symmetric providers share document/query cache entries. Asymmetric providers
        # retain their query operation and use a separate cache/in-flight namespace.
        return await self._embed(texts, query=isinstance(self._model, QueryEmbeddingModel))

    async def _embed(self, texts: tuple[str, ...], *, query: bool) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))
        if not all(isinstance(text, str) for text in texts):
            raise ValueError("Embedding inputs must be strings")  # noqa: TRY003
        loop = asyncio.get_running_loop()
        results: dict[str, _Embedded] = {}
        waiting: dict[str, asyncio.Future[_Embedded]] = {}
        for text in texts:
            key = (query, text)
            if key in self._cache:
                self._cache.move_to_end(key)
                results[text] = _Embedded(self._cache[key])
                continue
            if text in waiting:
                continue
            future = self._inflight.get(key)
            if future is None:
                future = loop.create_future()
                future.add_done_callback(_observe_failure)
                self._inflight[key] = future
                self._pending[query].append(key)
            waiting[text] = future
        if any(self._pending) and (self._dispatch_task is None or self._dispatch_task.done()):
            self._dispatch_task = asyncio.create_task(self._dispatch(), name="powercontext-embedding-coalescer")
        if waiting:
            embedded = await asyncio.gather(*(asyncio.shield(future) for future in waiting.values()))
            results.update(zip(waiting, embedded, strict=True))
        for text in texts:
            self._remember((query, text), results[text].vector)
        return EmbeddingResult(vectors=tuple(results[text].vector for text in texts), usage=_combined_usage(results))

    def _remember(self, key: _Key, vector: EmbeddingVector) -> None:
        if self._cache_size == 0:
            return
        self._cache[key] = vector
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    async def _dispatch(self) -> None:
        try:
            # Even a zero-length window yields once so simultaneous callers can register.
            await asyncio.sleep(self._delay)
            for queue in self._pending:
                while queue:
                    keys = tuple(queue.popleft() for _ in range(min(len(queue), self._max_batch_size)))
                    self._batch_id += 1
                    worker = asyncio.create_task(self._run_batch(keys, self._batch_id))
                    self._workers.add(worker)
                    worker.add_done_callback(self._workers.discard)
        except asyncio.CancelledError:
            for queue in self._pending:
                while queue:
                    future = self._inflight.pop(queue.popleft(), None)
                    if future is not None:
                        future.cancel()
            raise
        finally:
            self._dispatch_task = None

    async def _run_batch(self, keys: tuple[_Key, ...], batch_id: int) -> None:
        futures = tuple(self._inflight[key] for key in keys)
        try:
            texts = tuple(key[1] for key in keys)
            result = await embed_query(self._model, texts) if keys[0][0] else await self._model.embed(texts)
            if len(result.vectors) != len(keys):
                raise ValueError("Embedding provider returned a different number of vectors")  # noqa: TRY003, TRY301
            for key, future, vector in zip(keys, futures, result.vectors, strict=True):
                self._remember(key, vector)
                self._inflight.pop(key)
                future.set_result(_Embedded(vector, batch_id, result.usage))
        except BaseException as error:
            for key, future in zip(keys, futures, strict=True):
                if self._inflight.get(key) is future:
                    self._inflight.pop(key)
                if not future.done():
                    if isinstance(error, asyncio.CancelledError):
                        future.cancel()
                    else:
                        future.set_exception(error)
            if not isinstance(error, Exception):
                raise


__all__ = ["CoalescingEmbeddingModel"]
