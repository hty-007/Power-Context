"""Offline providers and public Runtime checks for count admission."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import unicodedata
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    GetMemoryEntryRequest,
    GetTopicMemoryRequest,
    SearchMemoryRequest,
    SearchTopicMemoryRequest,
)
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.builtin.scope import ScopeDraft
from pydantic import AnyHttpUrl

PUBLIC = Path(__file__).parents[1] / "workload/public/messages.json"
WAIT_SECONDS = 45
QUIET_SECONDS = 5
CAPTURED_INPUTS = {}


def messages(path: Path = PUBLIC) -> list[CaptureSource]:
    rows = json.loads(path.read_text())["records"]
    return [
        CaptureSource(source_id=row["source_id"], content=row["text"], metadata={"role": row["role"]}) for row in rows
    ]


class InferenceServer(ThreadingHTTPServer):
    """Respond using only the evidence supplied by each native generation request."""

    def __init__(self):
        super().__init__(("127.0.0.1", 0), InferenceHandler)
        self.generation_calls = 0
        self.fail_generation = False
        self.evidence_requests = []


class InferenceHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.endswith("/embeddings"):
            inputs = payload["input"]
            items = [inputs] if isinstance(inputs, str) else inputs
            response = {
                "object": "list",
                "data": [{"object": "embedding", "index": i, "embedding": [1.0, 0.0]} for i, _ in enumerate(items)],
                "model": "count-test-embedding",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        else:
            self.server.generation_calls += 1
            if self.server.fail_generation:
                self.send_error(503, "Controlled offline provider interruption")
                return
            prompts = [m["content"] for m in payload["messages"] if m["role"] == "user"]
            value = json.loads(prompts[-1])
            evidence = value.get("evidence", [])
            self.server.evidence_requests.extend(evidence)
            if "current_entries" in value:
                output = {
                    "candidates": [
                        {
                            "intent": "add",
                            "kind": "fact",
                            "text": self.evidence_text(item),
                            "evidence_ids": [item["evidence_id"]],
                        }
                        for item in evidence
                    ]
                }
            elif "component_id" in value:
                output = {"proposals": value["proposals"]}
            elif "historical" in value:
                output = {
                    "ambiguous": False,
                    "proposals": [
                        {
                            "content": {
                                "title": self.evidence_text(item)[:100],
                                "summary": self.evidence_text(item),
                                "detail": self.evidence_text(item),
                            },
                            "evidence_ids": [item["evidence_id"]],
                        }
                        for item in evidence
                    ],
                }
            else:
                output = {
                    "probes": [
                        {"query": "CountEvidence", "keywords": [], "evidence_ids": [item["evidence_id"]]}
                        for item in evidence
                    ]
                }
            response = {
                "id": "offline-count",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps(output)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            }
        data = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def evidence_text(item):
        content = item["content"]
        text = content if isinstance(content, str) else json.dumps(content, sort_keys=True, ensure_ascii=False)
        return "CountEvidence " + hashlib.sha256(text.encode()).hexdigest()[:16] + " " + text[:600]


@contextmanager
def provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-count-fixture")
    server = InferenceServer()
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def config(database: Path, server, *, families=("memory",), **settings) -> BuiltinConfig:
    assert "memory_source_count_threshold" in RuntimeConfig.model_fields, "Missing Memory count configuration"
    assert "topic_memory_source_count_threshold" in RuntimeConfig.model_fields, "Missing Topic count configuration"
    endpoint = AnyHttpUrl(f"http://127.0.0.1:{server.server_port}/v1")
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
        inference=InferenceConfig(
            generation_model="openai-chat:gpt-4o-mini",
            generation_base_url=endpoint,
            embedding_model="openai:count-test-embedding",
            embedding_base_url=endpoint,
            embedding_dimension=2,
            embedding_profile_id="count-test",
        ),
        runtime=RuntimeConfig(
            artifact_processing_families=families,
            artifact_processing_worker_timeout_seconds=30,
            memory_worker_timeout_seconds=30,
            topic_memory_worker_timeout_seconds=30,
            **settings,
        ),
    )


async def scope(runtime, key="main"):
    return (
        await runtime.scopes.create(
            ScopeDraft(title=key, summary="Licensed conversation count fixture", idempotency_key=key)
        )
    ).scope_id


async def capture(runtime, scope_id, items):
    receipts = []
    for item in items:
        receipt = await runtime.sources.for_scope(scope_id).capture(item)
        CAPTURED_INPUTS[scope_id, "content", item.source_id] = item
        receipts.append(receipt)
    return receipts


def ref_key(ref):
    return ref.source_type, ref.source_id


def receipt_refs(receipts):
    return {ref_key(item.source_ref) for item in receipts}


async def source_refs(runtime, scope_id, family):
    if family == "memory":
        rows = (await runtime.memory.for_scope(scope_id).list()).entries
        return {ref_key(ref) for row in rows for ref in row.entry.sources}
    app = runtime.topic_memory.for_scope(scope_id)
    rows = await app.browse(limit=100)
    topics = [await app.get(GetTopicMemoryRequest(artifact=row.artifact_ref)) for row in rows]
    return {ref_key(ref) for item in topics for ref in item.topic.lineage.sources}


async def wait_refs(runtime, scope_id, family, expected):
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        actual = await source_refs(runtime, scope_id, family)
        if actual == expected:
            return
        assert actual <= expected, ("Unexpected consumed Source", actual, expected)
        await asyncio.sleep(0.15)
    assert await source_refs(runtime, scope_id, family) == expected, "Native automatic processing timed out"


async def stay_refs(runtime, scope_id, family, expected):
    deadline = time.monotonic() + QUIET_SECONDS
    while time.monotonic() < deadline:
        assert await source_refs(runtime, scope_id, family) == expected
        await asyncio.sleep(0.1)


async def check_reads(runtime, scope_id, family, expected):
    assert await source_refs(runtime, scope_id, family) == expected
    records = runtime.records.for_scope(scope_id)
    expected_texts = {}
    for source_type, source_id in expected:
        record = await records.get_source(source_type, source_id)
        assert (record.source_type, record.source_id) == (source_type, source_id)
        command = CAPTURED_INPUTS[scope_id, source_type, source_id]
        assert record.content == command.content
        if family == "memory":
            projected = {
                "source_type": source_type,
                "source_id": source_id,
                "content": command.content,
                "metadata": dict(command.metadata),
            }
        else:
            projected = json.dumps(
                {"content": command.content, "metadata": dict(command.metadata)},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        text = InferenceHandler.evidence_text({"content": projected})
        if family == "memory":
            text = unicodedata.normalize("NFC", text).strip()
        expected_texts.setdefault(text, set()).add((source_type, source_id))
    if family == "memory":
        app = runtime.memory.for_scope(scope_id)
        rows = (await app.list()).entries
        hits = (await app.search(SearchMemoryRequest(query="CountEvidence", mode="fts", limit=100))).hits
        assert rows

        def identity(ref):
            return ref.family, ref.artifact_id, ref.revision

        assert {(identity(hit.memory_ref), hit.entry_id, hit.entry_version_id, hit.text) for hit in hits} == {
            (identity(row.memory_ref), row.entry.entry_id, row.entry.entry_version_id, row.entry.text) for row in rows
        }
        for row in rows:
            exact = await app.get(GetMemoryEntryRequest(citation=row.citation))
            assert exact.entry == row.entry
            assert exact.entry.text in expected_texts
            assert {ref_key(ref) for ref in exact.entry.sources} <= expected_texts[exact.entry.text]
    else:
        app = runtime.topic_memory.for_scope(scope_id)
        rows = await app.browse(limit=100)
        hits = (await app.search(SearchTopicMemoryRequest(query="CountEvidence", limit=20), allow_embedding=False)).hits
        assert rows
        assert {(hit.artifact_ref.artifact_id, hit.artifact_ref.revision, hit.title, hit.summary) for hit in hits} == {
            (row.artifact_ref.artifact_id, row.artifact_ref.revision, row.title, row.summary) for row in rows
        }
        for row in rows:
            exact = await app.get(GetTopicMemoryRequest(artifact=row.artifact_ref))
            assert exact.topic.content.detail in expected_texts
            assert exact.topic.content.summary == exact.topic.content.detail
            assert exact.topic.content.title == exact.topic.content.detail[:100]
            assert {ref_key(ref) for ref in exact.topic.lineage.sources} <= expected_texts[exact.topic.content.detail]
