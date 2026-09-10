"""Tests for the embedded dispatcher's per-tick helpers (gateway/kanban_watchers_dispatcher.py)."""

from __future__ import annotations

import asyncio
import time

from gateway.kanban_watchers_common import _to_thread_process_service
from gateway.kanban_watchers_dispatcher import _KanbanDispatcher, _run_decompose_and_dispatch


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


def test_auto_decompose_board_pin_does_not_leak_into_concurrent_tick(monkeypatch, tmp_path):
    """auto_decompose_tick pins the board it's currently decomposing so the
    decomposer (which connects with no explicit board kwarg) hits the right
    board's DB. Now that it runs concurrently with tick_once (each offloaded
    via _to_thread_process_service, gateway/kanban_watchers_common.py, into
    its own worker thread with a fresh contextvars.Context), that pin must
    stay confined to its own thread. A process-global pin (os.environ, as it
    used to be) would instead bleed into tick_once's thread and make
    lifecycle hooks fired mid-tick -- e.g. kanban_task_claimed via
    _fire_task_hook -> get_current_board() (hermes_cli/kanban_db.py) -- report
    whatever board auto-decompose happened to be pinned to, not the board the
    claim actually happened on. See the PR review for #106985."""
    from hermes_cli import kanban_db, kanban_decompose

    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path))
    other_board_dir = tmp_path / "kanban" / "boards" / "other-board"
    other_board_dir.mkdir(parents=True)
    (other_board_dir / "board.json").write_text("{}")

    def _slow_list_triage_ids(*, tenant=None):
        time.sleep(0.3)  # hold the pin while a concurrent tick reads the board
        return []

    monkeypatch.setattr(kanban_decompose, "list_triage_ids", _slow_list_triage_ids)

    fake_kb = type("_FakeKb", (), {
        "list_boards": staticmethod(lambda include_archived=False: [{"slug": "other-board"}]),
    })()
    dispatcher = _KanbanDispatcher(fake_kb, settings=None)

    async def _read_board_mid_decompose():
        await asyncio.sleep(0.1)  # land squarely inside the decompose pin window
        return await _to_thread_process_service(kanban_db.get_current_board)

    async def _run():
        return await asyncio.gather(
            _to_thread_process_service(dispatcher.auto_decompose_tick, 5),
            _read_board_mid_decompose(),
        )

    _, observed_board = asyncio.run(_run())

    assert observed_board != "other-board", (
        f"get_current_board() resolved to {observed_board!r} on a thread that "
        "never touched 'other-board' -- the auto-decompose board pin leaked "
        "across the concurrent tick, exactly what would misattribute "
        "kanban_task_claimed/_completed/_blocked hook payloads to the wrong board"
    )
