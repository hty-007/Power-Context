from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.http_reranker import HTTPMemoryReranker
from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts


def _hit(rank: int) -> MemoryHit:
    return MemoryHit(
        memory_ref=ArtifactRef(family="memory", artifact_id="m", revision=1),
        entry_id=f"e{rank}",
        entry_version_id=f"v{rank}",
        text=f"candidate {rank}",
        score=1.0 / rank,
        matched_by=("fts",),
    )


def test_batches_and_globally_orders_scores() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["query"] == "query"
        assert 1 <= len(body["candidates"]) <= 2
        for item in body["candidates"]:
            assert type(item["rank"]) is int and 1 <= item["rank"] <= 3
            assert item["text"] == _hit(item["rank"]).text
        seen.append(body)
        scores = [
            {"rank": item["rank"], "score": {1: 0.2, 2: 0.9, 3: 0.9}[item["rank"]]} for item in body["candidates"]
        ]
        return httpx.Response(200, json={"scores": scores})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=2)
            decision = await reranker.rerank("query", tuple(_hit(i) for i in range(1, 4)), 2)
        assert decision.selected_ranks == (2, 3)
        assert decision.usage.requests == 2
        assert len(seen) == 2
        assert sorted(item["rank"] for request in seen for item in request["candidates"]) == [1, 2, 3]

    asyncio.run(scenario())


def test_rejects_extra_or_missing_schema_fields() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={"scores": [], "extra": True}))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://invalid", client=client).rerank("q", (_hit(1),), 1)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {"scores": [{"rank": 2, "score": 0.5}]},
        {"scores": [{"rank": 1, "score": 0.5}, {"rank": 1, "score": 0.4}]},
        {"scores": [{"rank": 1, "score": float("nan")}]},
        {"scores": [{"rank": 1, "score": float("inf")}]},
        {"scores": [{"rank": 1, "score": 0.5, "extra": True}]},
    ],
)
def test_rejects_invalid_rank_coverage_and_scores(payload) -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=json.dumps(payload).encode()))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://invalid", client=client).rerank("q", (_hit(1),), 1)

    asyncio.run(scenario())


def test_accepts_out_of_order_scores_and_validates_bounds() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                200, json={"scores": [{"rank": 2, "score": 0.1}, {"rank": 1, "score": 0.8}]}
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            reranker = HTTPMemoryReranker("https://invalid", client=client, batch_size=2)
            assert (await reranker.rerank("q", (_hit(1), _hit(2)), 1)).selected_ranks == (1,)
            with pytest.raises(ValueError):
                await reranker.rerank("q", (), 1)
            with pytest.raises(ValueError):
                await reranker.rerank("q", (_hit(1),), 2)
            with pytest.raises(ValueError):
                HTTPMemoryReranker("https://invalid", client=client, batch_size=0)

    asyncio.run(scenario())


def test_preserves_http_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(httpx.ConnectError):
                await HTTPMemoryReranker("https://invalid", client=client).rerank("q", (_hit(1),), 1)

    asyncio.run(scenario())


def test_reranker_runs_inside_native_memory_search() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={"scores": [{"rank": item["rank"], "score": float(item["rank"])} for item in body["candidates"]]},
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=1)
            async with open_builtin_contexts(
                BuiltinConfig(database=SQLiteConfig()), memory_reranker=reranker
            ) as contexts:
                service = (await contexts.get("retrieval-01-reranking-native")).artifacts.memory
                memory = await service.remember(
                    memory=None,
                    entries=(
                        MemoryEntryInput(kind="fact", text="Project alpha fact."),
                        MemoryEntryInput(kind="fact", text="Project beta fact."),
                    ),
                    mode="append",
                )
                assert memory is not None
                result = await service.search("project", memories=(memory,), limit=1, mode="fts")
                assert result.rerank is not None
                assert result.hits[0].entry_version_id == result.rerank.candidate_hits[-1].entry_version_id

    asyncio.run(scenario())
