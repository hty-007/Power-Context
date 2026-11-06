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

"""Batch HTTP scoring while preserving the original global Memory candidate ranks."""

from __future__ import annotations

from typing import Annotated

import httpx
from pydantic import BaseModel, ConfigDict, Field

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage


class _Score(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: Annotated[int, Field(strict=True)]
    score: Annotated[float, Field(strict=True, allow_inf_nan=False)]


class _ScoringResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scores: list[_Score]


class HTTPMemoryReranker:
    """Use a caller-owned AsyncClient to score every candidate exactly once."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("HTTP rerank batch_size must be a positive integer")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(self, query: str, candidates: tuple[MemoryHit, ...], limit: int, /) -> MemoryRerankDecision:
        if not candidates or type(limit) is not int or not 1 <= limit <= len(candidates):
            raise ValueError("HTTP rerank requires candidates and a valid integer limit")  # noqa: TRY003
        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            expected = set(range(start + 1, start + len(batch) + 1))
            requests += 1
            response = await self._client.post(
                self._endpoint,
                json={
                    "query": query,
                    "candidates": [
                        {"rank": rank, "text": candidate.text} for rank, candidate in enumerate(batch, start=start + 1)
                    ],
                },
            )
            response.raise_for_status()
            payload = _ScoringResponse.model_validate_json(response.content)
            ranks = [item.rank for item in payload.scores]
            if len(ranks) != len(expected) or set(ranks) != expected:
                raise ValueError("HTTP rerank response must score every batch rank exactly once")  # noqa: TRY003
            scores.update((item.rank, item.score) for item in payload.scores)
        return MemoryRerankDecision(
            selected_ranks=tuple(sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit]),
            usage=InferenceUsage(requests=requests),
        )


__all__ = ["HTTPMemoryReranker"]
