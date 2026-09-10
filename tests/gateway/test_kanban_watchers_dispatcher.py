"""Tests for the embedded dispatcher's per-tick helpers (gateway/kanban_watchers_dispatcher.py)."""

from __future__ import annotations

import asyncio
import time

from gateway.kanban_watchers_dispatcher import _run_decompose_and_dispatch


class _FakeDispatcher:
    """Stand-in for _KanbanDispatcher: records call timing, sleeps to simulate
    a slow/stuck auto-decompose call against an aux LLM."""

    def __init__(self, decompose_seconds: float) -> None:
        self.decompose_seconds = decompose_seconds
        self.tick_once_started_at: float | None = None

    def auto_decompose_tick(self, auto_decompose_per_tick: int) -> int:
        time.sleep(self.decompose_seconds)
        return 0

    def tick_once(self) -> list:
        self.tick_once_started_at = time.monotonic()
        return [("default", None)]


def test_spawn_tick_does_not_wait_for_slow_decompose():
    """The spawn step (tick_once) must start immediately, not after a slow
    auto_decompose_tick finishes -- a slow/stuck triage decompose call must
    not delay unrelated ready tasks from being spawned (#106985)."""
    dispatcher = _FakeDispatcher(decompose_seconds=0.5)
    start = time.monotonic()

    asyncio.run(_run_decompose_and_dispatch(dispatcher, True, 3))

    tick_once_delay = dispatcher.tick_once_started_at - start
    assert tick_once_delay < 0.25, (
        f"tick_once started {tick_once_delay:.3f}s after the tick began; "
        "it should start immediately, concurrently with auto_decompose_tick"
    )


def test_decompose_disabled_skips_straight_to_dispatch():
    dispatcher = _FakeDispatcher(decompose_seconds=0.0)

    results = asyncio.run(_run_decompose_and_dispatch(dispatcher, False, 3))

    assert results == [("default", None)]
