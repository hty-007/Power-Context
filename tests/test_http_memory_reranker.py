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

import httpx
import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory.http_reranker import HTTPMemoryReranker
from powercontext.builtin.artifacts.memory.models import MemoryHit


def hit(rank: int) -> MemoryHit:
    return MemoryHit(
        memory_ref=ArtifactRef(family="memory", artifact_id="memory", revision=1),
        entry_id=f"entry-{rank}",
        entry_version_id=f"version-{rank}",
        text=f"Original candidate {rank}",
        score=1 / rank,
        matched_by=("fts",),
    )


def test_many_batches_preserve_global_ranks_and_stable_ties() -> None:
    requests = []

    def handler(request):
        body = json.loads(request.content)
        assert set(body) == {"query", "candidates"}
        assert len(body["candidates"]) <= 7
        requests.append(body)
        return httpx.Response(
            200,
            json={"scores": [{"rank": candidate["rank"], "score": 0.5} for candidate in reversed(body["candidates"])]},
        )

    async def scenario():
        candidates = tuple(hit(rank) for rank in range(1, 51))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            decision = await HTTPMemoryReranker("https://test.invalid", client=client, batch_size=7).rerank(
                "query", candidates, 10
            )
            assert not client.is_closed
        assert decision.selected_ranks == tuple(range(1, 11))
        assert decision.usage.requests == len(requests) == 8
        assert [item["rank"] for request in requests for item in request["candidates"]] == list(range(1, 51))

    asyncio.run(scenario())


@pytest.mark.parametrize("rank,score", [(True, 1), (1.0, 1), ("1", 1), (1, True), (1, "1")])
def test_json_numbers_are_strict(rank, score) -> None:
    async def scenario():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"scores": [{"rank": rank, "score": score}]})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://test.invalid", client=client).rerank("query", (hit(1),), 1)

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [301, 400, 503])
def test_non_success_status_preserves_httpx_error(status: int) -> None:
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status))) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await HTTPMemoryReranker("https://test.invalid", client=client).rerank("query", (hit(1),), 1)

    asyncio.run(scenario())
