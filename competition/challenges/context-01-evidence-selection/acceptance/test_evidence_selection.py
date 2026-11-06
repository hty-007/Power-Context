from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("selection_runner", Path(__file__).with_name("runner.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.mark.asyncio
async def test_query_drives_selection_of_late_literal_evidence():
    prefix = "General administrative notes. " * 70
    first = {"id": "alpha", "text": prefix + "The cobalt sensor is calibrated at 37 degrees. "}
    second = {"id": "beta", "text": prefix + "The amber valve opens at 19 degrees. "}
    inputs = [
        {
            "case_id": "a",
            "query": "cobalt sensor calibrated",
            "documents": [first, second],
            "max_bytes": 1200,
            "selection": True,
        },
        {
            "case_id": "b",
            "query": "amber valve opens",
            "documents": [first, second],
            "max_bytes": 1200,
            "selection": True,
        },
    ]
    results = await runner.evaluate(inputs)
    assert any(fragment["document_id"] == "alpha" and "37" in fragment["text"] for fragment in results[0]["fragments"])
    assert any(fragment["document_id"] == "beta" and "19" in fragment["text"] for fragment in results[1]["fragments"])


@pytest.mark.asyncio
async def test_unicode_budget_and_default_prepare_are_observable():
    inputs = [
        {
            "case_id": "unicode",
            "query": "\u6d77\u6d0b\u89c2\u6d4b station",
            "documents": [{"id": "one", "text": "\u6d77\u6d0b\u89c2\u6d4b station records temperatures. " * 40}],
            "max_bytes": 1200,
            "selection": True,
        },
        {
            "case_id": "ordinary",
            "query": "temperature",
            "documents": [{"id": "one", "text": "A station records temperature."}],
            "max_bytes": 1200,
            "selection": False,
        },
    ]
    results = await runner.evaluate(inputs)
    assert results[0]["fragments"] and results[0]["bytes"] <= 1200
    assert results[1]["fragments"][0]["text"] == "A station records temperature."


@pytest.mark.asyncio
async def test_public_scifact_inputs_flow_through_real_memory():
    import json

    sample = json.loads((Path(__file__).parents[1] / "workload/public/scifact-sample.json").read_text())
    query = sample["queries"][0]
    documents = [
        {"id": document["id"], "text": document["title"] + " " + document["text"]} for document in sample["documents"]
    ]
    observed = await runner.evaluate(
        [
            {
                "case_id": "public-scifact",
                "query": query["text"],
                "documents": documents,
                "max_bytes": 1800,
                "selection": True,
            }
        ]
    )
    assert observed[0]["fragments"] and observed[0]["bytes"] <= 1800
