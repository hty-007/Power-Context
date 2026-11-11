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

"""Count-based admission for automatic Source processing.

A Scope that only ever waits for a time condition can sit idle while it accumulates input, and
one that receives a burst should not pay a generation per entry. This watcher starts one ordinary
processing window as soon as enough journal identities are queued behind the family's committed
Source cursor. It adds no new processing machinery: it reads the same cursor the time condition
reads and calls the same window the time condition calls.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from time import monotonic
from typing import Final

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.runtime.recall_sufficiency import MEMORY_FAMILY, TOPIC_MEMORY_FAMILY
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME

logger = logging.getLogger(__name__)

FAMILY_CURSORS: Final[Mapping[str, str]] = {
    MEMORY_FAMILY: SOURCE_WINDOW_TRIGGER_NAME,
    TOPIC_MEMORY_FAMILY: TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
}

SWEEP_SECONDS: Final = 0.2
FAILURE_BACKOFF_SECONDS: Final = 1.0

ScopeIds = Callable[[], Awaitable[tuple[str, ...]]]
PendingCounter = Callable[[str, str], Awaitable[int]]
WindowProcessor = Callable[[str, str], Awaitable[None]]


class SourceCountTrigger:
    """Start one ordinary processing window when a Scope crosses its count threshold."""

    def __init__(
        self,
        *,
        scope_ids: ScopeIds,
        count_pending: PendingCounter,
        process_window: WindowProcessor,
        thresholds: Mapping[str, int],
        sweep_seconds: float = SWEEP_SECONDS,
        failure_backoff_seconds: float = FAILURE_BACKOFF_SECONDS,
    ) -> None:
        self._scope_ids = scope_ids
        self._count_pending = count_pending
        self._process_window = process_window
        self._thresholds = dict(thresholds)
        self._sweep_seconds = sweep_seconds
        self._failure_backoff_seconds = failure_backoff_seconds
        self._inflight: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._retry_at: dict[tuple[str, str], float] = {}
        self._task: asyncio.Task[None] | None = None
        self._closing = False

    def start(self) -> None:
        """Begin watching; the first sweep also resumes accepted work left by an earlier run."""

        if self._task is None and self._thresholds:
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def aclose(self) -> None:
        """Stop watching and cancel processing windows this watcher started."""

        self._closing = True
        watcher, self._task = self._task, None
        if watcher is not None:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
        windows = tuple(self._inflight.values())
        for window in windows:
            window.cancel()
        for window in windows:
            with suppress(asyncio.CancelledError, Exception):
                await window
        self._inflight.clear()
        self._retry_at.clear()

    async def _run(self) -> None:
        while not self._closing:
            try:
                await self._sweep()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - one failing sweep must not stop admission
                # An unrelated read failure says nothing about a particular Scope, so report it
                # and keep the watcher alive; per-Scope processing failures back off separately.
                logger.warning("Source count sweep failed", exc_info=error)
            await asyncio.sleep(self._sweep_seconds)

    async def _sweep(self) -> None:
        for scope_id in await self._scope_ids():
            if self._closing:
                return
            for family, threshold in self._thresholds.items():
                key = (scope_id, family)
                if key in self._inflight or self._backoff_active(key):
                    continue
                if await self._count_pending(scope_id, family) < threshold:
                    continue
                if self._closing:
                    return
                self._inflight[key] = asyncio.get_running_loop().create_task(self._admit(key))

    async def _admit(self, key: tuple[str, str]) -> None:
        scope_id, family = key
        try:
            await self._process_window(scope_id, family)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - the processor owns its own retry contract
            # Failure does not consume the committed cursor, so the queued identities stay
            # countable. Pause this key briefly instead of retrying on every sweep.
            self._retry_at[key] = monotonic() + self._failure_backoff_seconds
            logger.warning(
                "Source count processing failed",
                exc_info=error,
                extra={"event": "runtime.source_count.failure", "outcome": "failure", "unit": family},
            )
        else:
            self._retry_at.pop(key, None)
        finally:
            self._inflight.pop(key, None)

    def _backoff_active(self, key: tuple[str, str]) -> bool:
        retry_at = self._retry_at.get(key)
        if retry_at is None:
            return False
        if monotonic() >= retry_at:
            self._retry_at.pop(key, None)
            return False
        return True


__all__ = [
    "FAMILY_CURSORS",
    "PendingCounter",
    "SWEEP_SECONDS",
    "ScopeIds",
    "SourceCountTrigger",
    "WindowProcessor",
]
