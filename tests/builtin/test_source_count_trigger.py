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

"""Behaviour tests for count-based Source admission."""

from __future__ import annotations

import asyncio

from powercontext.builtin.runtime.source_count import MEMORY_FAMILY, TOPIC_MEMORY_FAMILY, SourceCountTrigger

SWEEP = 0.01
BACKOFF = 0.05


class Harness:
    """Fake Runtime surface: per-key pending counts and recorded windows."""

    def __init__(self) -> None:
        self.scopes = ("scope-a",)
        self.pending: dict[tuple[str, str], int] = {}
        self.calls: list[tuple[str, str]] = []
        self.failures = 0
        self.remaining_failures = 0
        self.gate: asyncio.Event | None = None
        self.entered: asyncio.Event | None = None

    async def scope_ids(self) -> tuple[str, ...]:
        return self.scopes

    async def count_pending(self, scope_id: str, family: str) -> int:
        return self.pending.get((scope_id, family), 0)

    async def process_window(self, scope_id: str, family: str) -> None:
        self.calls.append((scope_id, family))
        if self.entered is not None:
            self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            self.failures += 1
            raise RuntimeError("controlled failure")
        # A successful window consumes what it read.
        self.pending[(scope_id, family)] = max(0, self.pending.get((scope_id, family), 0) - 1)


def trigger(harness: Harness, thresholds: dict[str, int]) -> SourceCountTrigger:
    return SourceCountTrigger(
        scope_ids=harness.scope_ids,
        count_pending=harness.count_pending,
        process_window=harness.process_window,
        thresholds=thresholds,
        sweep_seconds=SWEEP,
        failure_backoff_seconds=BACKOFF,
    )


async def settle(seconds: float = SWEEP * 12) -> None:
    await asyncio.sleep(seconds)


def test_below_threshold_never_starts_a_window() -> None:
    async def scenario() -> None:
        harness = Harness()
        harness.pending[("scope-a", MEMORY_FAMILY)] = 2
        admission = trigger(harness, {MEMORY_FAMILY: 3})
        admission.start()
        try:
            await settle()
        finally:
            await admission.aclose()
        assert harness.calls == []

    asyncio.run(scenario())


def test_threshold_starts_one_window_and_rechecks_the_remainder() -> None:
    async def scenario() -> None:
        harness = Harness()
        # Three queued identities, a one-entry window, threshold three: one window runs, the
        # remaining two stay below the threshold and wait for more input.
        harness.pending[("scope-a", MEMORY_FAMILY)] = 3
        admission = trigger(harness, {MEMORY_FAMILY: 3})
        admission.start()
        try:
            await settle()
        finally:
            await admission.aclose()
        assert harness.calls == [("scope-a", MEMORY_FAMILY)]
        assert harness.pending[("scope-a", MEMORY_FAMILY)] == 2

    asyncio.run(scenario())


def test_above_threshold_rechecks_until_below() -> None:
    async def scenario() -> None:
        harness = Harness()
        harness.pending[("scope-a", MEMORY_FAMILY)] = 6
        admission = trigger(harness, {MEMORY_FAMILY: 3})
        admission.start()
        try:
            await settle(SWEEP * 40)
        finally:
            await admission.aclose()
        # Each window consumes one entry; admission stops as soon as three remain queued.
        assert len(harness.calls) >= 2
        assert harness.pending[("scope-a", MEMORY_FAMILY)] == 2

    asyncio.run(scenario())


def test_scopes_and_families_are_independent() -> None:
    async def scenario() -> None:
        harness = Harness()
        harness.scopes = ("scope-a", "scope-b")
        harness.pending[("scope-a", MEMORY_FAMILY)] = 2
        harness.pending[("scope-b", TOPIC_MEMORY_FAMILY)] = 3
        harness.pending[("scope-b", MEMORY_FAMILY)] = 1
        admission = trigger(harness, {MEMORY_FAMILY: 2, TOPIC_MEMORY_FAMILY: 3})
        admission.start()
        try:
            await settle()
        finally:
            await admission.aclose()
        assert sorted(harness.calls) == [("scope-a", MEMORY_FAMILY), ("scope-b", TOPIC_MEMORY_FAMILY)]

    asyncio.run(scenario())


def test_failure_is_retried_after_a_backoff() -> None:
    async def scenario() -> None:
        harness = Harness()
        harness.pending[("scope-a", MEMORY_FAMILY)] = 2
        harness.remaining_failures = 1
        admission = trigger(harness, {MEMORY_FAMILY: 2})
        admission.start()
        try:
            await settle(SWEEP * 60)
        finally:
            await admission.aclose()
        assert harness.failures == 1
        assert len(harness.calls) == 2, "a failure must keep the queued input and retry"
        assert harness.pending[("scope-a", MEMORY_FAMILY)] == 1

    asyncio.run(scenario())


def test_one_window_runs_at_a_time_per_scope_and_family() -> None:
    async def scenario() -> None:
        harness = Harness()
        harness.pending[("scope-a", MEMORY_FAMILY)] = 5
        harness.gate = asyncio.Event()
        harness.entered = asyncio.Event()
        admission = trigger(harness, {MEMORY_FAMILY: 2})
        admission.start()
        await harness.entered.wait()
        await settle(SWEEP * 20)
        assert len(harness.calls) == 1, "a queued window must not be started twice"
        harness.gate.set()
        await settle(SWEEP * 20)
        await admission.aclose()
        assert len(harness.calls) >= 2

    asyncio.run(scenario())


def test_disabled_thresholds_start_no_work() -> None:
    async def scenario() -> None:
        harness = Harness()
        harness.pending[("scope-a", MEMORY_FAMILY)] = 100
        admission = trigger(harness, {})
        admission.start()
        try:
            await settle()
        finally:
            await admission.aclose()
        assert harness.calls == []
        assert admission._task is None

    asyncio.run(scenario())
