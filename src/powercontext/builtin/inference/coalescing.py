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

"""Cross-call request coalescing and a bounded vector cache for embedding models.

Several callers in one event loop may embed the same text at the same moment. This
wrapper lets them share one unfinished underlying request and keeps successful
vectors in a bounded LRU cache. Callers stay isolated: cancelling one waiter never
cancels the shared work, a failed batch never poisons the cache, and every caller is
charged only for the distinct underlying batches it actually waited on.
"""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Final

from powercontext.builtin.inference.models import EmbeddingResult, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel, embed_query

_DOCUMENT: Final = "document"
_QUERY: Final = "query"


@dataclass(frozen=True, slots=True)
class _Outcome:
    """One vector together with the underlying batch that produced it."""

    vector: tuple[float, ...]
    batch: int
    usage: InferenceUsage


class CoalescingEmbeddingModel:
    """Share unfinished embedding requests and cache successful vectors across calls."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        self._max_batch_size = _positive_int(max_batch_size, "max_batch_size")
        self._coalesce_seconds = _milliseconds(coalesce_ms)
        self._cache_size = _non_negative_int(cache_size, "cache_size")
        self._delegate = model
        self.profile = model.profile
        # Work is shared per text: the same text embedded as a document and as a retrieval query
        # produces one underlying request. Only the request kind a batch is dispatched with
        # differs, so an asymmetric provider still receives each call on the right method.
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._flights: dict[str, asyncio.Future[_Outcome]] = {}
        self._pending: dict[str, str] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._batch = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._dispatcher: asyncio.Task[None] | None = None

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return one ordered vector per input text, reusing shared work where possible."""

        return await self._embed(texts, document=True)

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed retrieval queries through the same coalescing and cache path."""

        return await self._embed(texts, document=False)

    async def aclose(self) -> None:
        """Stop the background dispatcher; unfinished waiters are released by the caller."""

        dispatcher, self._dispatcher = self._dispatcher, None
        if dispatcher is not None:
            dispatcher.cancel()
            with suppress(asyncio.CancelledError):
                await dispatcher

    async def _embed(self, texts: Sequence[str], *, document: bool) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))
        loop = self._bind_loop()
        keys = tuple(texts)
        kind = _DOCUMENT if document else _QUERY
        hits: dict[str, tuple[float, ...]] = {}
        waited: list[str] = []
        pending: list[asyncio.Future[_Outcome]] = []
        for key in dict.fromkeys(keys):
            vector = self._cache.get(key)
            if vector is not None:
                self._cache.move_to_end(key)
                # Keep the value here: concurrent calls may evict this entry from the bounded
                # cache while this call is still waiting for its own batches.
                hits[key] = vector
                continue
            future = self._flights.get(key)
            if future is None:
                future = loop.create_future()
                self._flights[key] = future
                self._pending[key] = kind
                self._request_dispatch(loop)
            waited.append(key)
            pending.append(future)
        resolved: dict[str, _Outcome] = {}
        if pending:
            # Shield every waiter: one caller being cancelled must not cancel the shared batch
            # or strip the result from the remaining waiters. Failures are collected rather than
            # raised by ``gather`` so independent batches still finish and no exception leaks.
            outcomes = await asyncio.gather(*(asyncio.shield(future) for future in pending), return_exceptions=True)
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    raise outcome
            resolved = dict(zip(waited, outcomes, strict=True))
        return self._result(keys, hits, resolved)

    def _result(
        self,
        keys: tuple[str, ...],
        hits: dict[str, tuple[float, ...]],
        resolved: dict[str, _Outcome],
    ) -> EmbeddingResult:
        vectors: list[tuple[float, ...]] = []
        batches: dict[int, InferenceUsage] = {}
        for key in keys:
            outcome = resolved.get(key)
            if outcome is not None:
                vectors.append(outcome.vector)
                batches.setdefault(outcome.batch, outcome.usage)
                continue
            vector = hits.get(key)
            if vector is None:
                raise RuntimeError("coalesced embedding result is unavailable")  # noqa: TRY003
            vectors.append(vector)
        usage = InferenceUsage(requests=0)
        for batch in sorted(batches):
            usage = _combine_usage(usage, batches[batch])
        return EmbeddingResult(vectors=tuple(vectors), usage=usage)

    def _bind_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            # Futures belong to one loop; a later loop cannot await an earlier loop's work.
            self._loop = loop
            self._wake = asyncio.Event()
            self._dispatcher = None
            self._flights.clear()
            self._pending.clear()
        return loop

    def _request_dispatch(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._wake is None:
            return
        self._wake.set()
        if self._dispatcher is None:
            self._dispatcher = loop.create_task(self._dispatch_loop())

    async def _dispatch_loop(self) -> None:
        try:
            while True:
                if self._wake is None:
                    return
                await self._wake.wait()
                self._wake.clear()
                await asyncio.sleep(self._coalesce_seconds)
                self._flush()
                if not self._pending:
                    return
                self._wake.set()
        finally:
            self._dispatcher = None

    def _flush(self) -> None:
        loop = self._loop
        if loop is None:
            return
        while self._pending:
            # One underlying request carries one kind: an asymmetric provider embeds documents
            # and retrieval queries through different methods, so a batch must never mix them.
            kind = next(iter(self._pending.values()))
            keys: list[str] = []
            for key, key_kind in self._pending.items():
                if key_kind != kind:
                    continue
                keys.append(key)
                if len(keys) >= self._max_batch_size:
                    break
            for key in keys:
                del self._pending[key]
            self._batch += 1
            task = loop.create_task(self._run_batch(tuple(keys), self._batch, kind))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_batch(self, keys: tuple[str, ...], batch: int, kind: str) -> None:
        try:
            if kind == _QUERY:
                result = await embed_query(self._delegate, keys)
            else:
                result = await self._delegate.embed(keys)
        except BaseException as error:  # noqa: BLE001 - every waiter observes the same failure
            for key in keys:
                self._settle(key, error)
            return
        if len(result.vectors) != len(keys):
            failure = ValueError("embedding vector count does not match the batch text count")
            for key in keys:
                self._settle(key, failure)
            return
        for key, vector in zip(keys, result.vectors, strict=True):
            outcome = _Outcome(tuple(vector), batch, result.usage)
            self._remember(key, outcome.vector)
            self._settle(key, outcome)

    def _settle(self, key: str, value: _Outcome | BaseException) -> None:
        future = self._flights.pop(key, None)
        if future is None or future.done():
            return
        if isinstance(value, BaseException):
            future.set_exception(value)
            # A caller cancelled while waiting never retrieves the failure; claim it here so the
            # event loop does not report an unretrieved exception for work nobody is left to pay.
            future.exception()
            return
        future.set_result(value)

    def _remember(self, key: str, vector: tuple[float, ...]) -> None:
        if self._cache_size == 0:
            return
        self._cache[key] = vector
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


def _combine_usage(left: InferenceUsage, right: InferenceUsage) -> InferenceUsage:
    """Add disjoint attempts without treating an unknown token count as zero."""

    if left.requests == 0:
        return right
    if right.requests == 0:
        return left
    return InferenceUsage(
        requests=left.requests + right.requests,
        input_tokens=_add(left.input_tokens, right.input_tokens),
        output_tokens=_add(left.output_tokens, right.output_tokens),
    )


def _add(left: int | None, right: int | None) -> int | None:
    return None if left is None or right is None else left + right


def _positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")  # noqa: TRY003
    return value


def _non_negative_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")  # noqa: TRY003
    return value


def _milliseconds(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("coalesce_ms must be a non-negative number")  # noqa: TRY003
    seconds = float(value) / 1000.0
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("coalesce_ms must be a non-negative number")  # noqa: TRY003
    return seconds


__all__ = ["CoalescingEmbeddingModel"]
