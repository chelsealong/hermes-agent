"""A parked server that never recovers must not flood the log with one WARNING per
self-probe (#115713): only the first parking transition is a WARNING, repeats are DEBUG,
with a periodic WARNING summary so the condition stays visible."""

import asyncio
import logging

import pytest


@pytest.mark.no_isolate
def test_permanently_dead_server_does_not_flood_warnings(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from tools import mcp_tool
    from tools.mcp_tool import MCPServerTask

    # Park on the very first failed attempt, and make the self-probe cadence + summary
    # cadence tiny so the test can exercise many park cycles fast.
    monkeypatch.setattr(mcp_tool, "_MAX_INITIAL_CONNECT_RETRIES", 0)
    monkeypatch.setattr(mcp_tool, "_PARKED_RETRY_INTERVAL", 0.01)
    monkeypatch.setattr(mcp_tool, "_PARK_LOG_SUMMARY_EVERY", 3)

    _real_sleep = asyncio.sleep

    async def _fast_sleep(_delay, *a, **kw):
        await _real_sleep(0)

    monkeypatch.setattr(mcp_tool.asyncio, "sleep", _fast_sleep)

    state = {"park_attempts": 0}

    async def _scenario():
        class _Task(MCPServerTask):
            def _is_http(self):
                return False

            def _deregister_tools(self):
                state["park_attempts"] += 1
                self._registered_tool_names = []

            async def _run_stdio(self, config):
                # A dependency that is never launched: every attempt fails, forever.
                raise ConnectionError("blender addon not running")

        task = _Task("blender")

        with caplog.at_level(logging.DEBUG, logger="tools.mcp_tool"):
            run_task = asyncio.ensure_future(task.run({"command": "x"}))
            # The first park comes off the fast-forwarded sleep(0) patch; each self-probe
            # after that waits out the real (tiny) _PARKED_RETRY_INTERVAL, so poll with real
            # sleeps for those.
            for _ in range(2000):
                await _real_sleep(0.02)
                if state["park_attempts"] >= 10:
                    break

        assert state["park_attempts"] >= 10, "server never cycled through enough park attempts"

        task._shutdown_event.set()
        task._reconnect_event.set()
        try:
            await asyncio.wait_for(run_task, timeout=15)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            run_task.cancel()

    asyncio.run(_scenario())

    park_warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and ("parked" in r.getMessage() or "still parked" in r.getMessage())
    ]
    park_debugs = [
        r for r in caplog.records
        if r.levelno == logging.DEBUG and "parking until a reconnect" in r.getMessage()
    ]

    attempts = state["park_attempts"]
    # One WARNING for the first parking transition, plus one every _PARK_LOG_SUMMARY_EVERY
    # repeats after that -- never one per attempt.
    expected_warnings = 1 + (attempts - 1) // 3
    assert len(park_warnings) == expected_warnings, (
        f"expected {expected_warnings} park WARNINGs for {attempts} attempts, got "
        f"{len(park_warnings)}: {[r.getMessage() for r in park_warnings]}"
    )
    assert len(park_warnings) < attempts, "park WARNINGs must not scale 1:1 with self-probe attempts"
    # The repeats that were not promoted to a summary WARNING must still be logged, at DEBUG.
    assert len(park_debugs) == (attempts - 1) - ((attempts - 1) // 3)
