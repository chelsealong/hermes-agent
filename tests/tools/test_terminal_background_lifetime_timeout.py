"""``terminal(background=true, timeout=N)`` must bound the process's lifetime.

Before this fix, ``timeout`` was silently dropped once ``background=true`` was
set: ``_plan_execution`` only used it to size the pre-exec guard, and
``spawn_background_process`` never received it at all. A background command
given an explicit timeout ran forever past it — the leaked-Chromium incident
in #116936 used ``timeout=30`` and the process was still alive 20 minutes
later. A promoted foreground call (over ``FOREGROUND_MAX_TIMEOUT``, forced
into the background) must NOT be killed at its requested `timeout`: that
value there means "how long the caller is willing to wait", not "when to
kill it" — the whole point of promoting instead of refusing.
"""

import sys
import time

import pytest

from tools.process_registry import ProcessRegistry
from tools.terminal_tool_background import _start_lifetime_watchdog


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture()
def registry():
    return ProcessRegistry()


def _spy_on_kill(registry, monkeypatch):
    """Wrap ``registry.kill_process`` and record every call, still delegating
    to the real implementation. A deterministic way to prove the watchdog did
    (or did not) fire, independent of ``ProcessSession.completion_reason``
    afterward — that field is racy against the reader thread by design (see
    ``kill_process``'s own "reader thread can finalise the session while the
    signal path blocks" comment) and is not what this fix is about."""
    calls = []
    real_kill = registry.kill_process

    def spy(session_id, **kwargs):
        calls.append((session_id, kwargs))
        return real_kill(session_id, **kwargs)

    monkeypatch.setattr(registry, "kill_process", spy)
    return calls


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")
class TestBackgroundLifetimeWatchdogKillsRealProcess:
    def test_watchdog_kills_a_still_running_process_after_its_timeout(self, registry, monkeypatch):
        session = registry.spawn_local(
            f"{sys.executable} -c \"import time; time.sleep(30)\"", cwd="/tmp",
        )
        assert session.process.poll() is None, "process must still be alive right after spawn"
        calls = _spy_on_kill(registry, monkeypatch)

        _start_lifetime_watchdog(registry, session, 0.3)

        assert _wait_until(lambda: session.process.poll() is not None, timeout=5.0), (
            "background process exceeded its timeout and must have been killed"
        )
        assert calls == [(session.id, {"source": "terminal.background_timeout", "consume_output": False})]

    def test_watchdog_leaves_a_process_that_exits_before_its_timeout(self, registry, monkeypatch):
        session = registry.spawn_local("echo hi", cwd="/tmp")
        calls = _spy_on_kill(registry, monkeypatch)

        _start_lifetime_watchdog(registry, session, 5.0)

        assert _wait_until(lambda: session.exited, timeout=5.0)
        time.sleep(0.2)  # let the watchdog thread wake on the completion event and return
        assert calls == [], "a process that finished on its own must not be killed by the watchdog"


# ---------------------------------------------------------------------------
# Wiring: terminal_tool() -> spawn_background_process(timeout=...)
# ---------------------------------------------------------------------------

def _harness(monkeypatch, tmp_path):
    import tools.terminal_tool as terminal_tool_module
    from tools import process_registry as process_registry_module
    from types import SimpleNamespace

    config = {
        "env_type": "local", "docker_image": "", "singularity_image": "",
        "modal_image": "", "daytona_image": "", "cwd": str(tmp_path), "timeout": 30,
    }
    dummy_env = SimpleNamespace(env={})

    def fake_spawn_local(**kwargs):
        return SimpleNamespace(
            id="proc_wiring_test", pid=4242, notify_on_complete=False,
            watcher_platform="", watcher_chat_id="", watcher_user_id="",
            watcher_user_name="", watcher_thread_id="", watcher_message_id="",
            watcher_interval=0,
        )

    monkeypatch.setattr(terminal_tool_module, "_get_env_config", lambda: config)
    monkeypatch.setattr(terminal_tool_module, "_start_cleanup_thread", lambda: None)
    monkeypatch.setattr(terminal_tool_module, "_check_all_guards", lambda *a, **k: {"approved": True})
    monkeypatch.setattr(process_registry_module.process_registry, "spawn_local", fake_spawn_local)
    monkeypatch.setitem(terminal_tool_module._active_environments, "default", dummy_env)
    monkeypatch.setitem(terminal_tool_module._last_activity, "default", 0.0)
    return terminal_tool_module


def test_explicit_background_call_forwards_timeout_to_the_watchdog(monkeypatch, tmp_path):
    tt = _harness(monkeypatch, tmp_path)
    captured = {}
    real_spawn = tt.spawn_background_process

    def spy(**kwargs):
        captured.update(kwargs)
        return real_spawn(**kwargs)

    monkeypatch.setattr(tt, "spawn_background_process", spy)
    monkeypatch.setattr("tools.terminal_tool_background._start_lifetime_watchdog", lambda *a, **k: None)
    try:
        tt.terminal_tool(command="sleep 999", background=True, timeout=42)
    finally:
        tt._active_environments.pop("default", None)
        tt._last_activity.pop("default", None)

    assert captured.get("timeout") == 42


def test_promoted_foreground_call_does_not_forward_timeout(monkeypatch, tmp_path):
    tt = _harness(monkeypatch, tmp_path)
    captured = {}
    real_spawn = tt.spawn_background_process

    def spy(**kwargs):
        captured.update(kwargs)
        return real_spawn(**kwargs)

    monkeypatch.setattr(tt, "spawn_background_process", spy)
    monkeypatch.setattr("tools.terminal_tool_background._start_lifetime_watchdog", lambda *a, **k: None)
    try:
        # Above FOREGROUND_MAX_TIMEOUT (600 by default) and background=False:
        # _plan_execution promotes this to a tracked background process.
        tt.terminal_tool(command="pytest tests/", background=False, timeout=900)
    finally:
        tt._active_environments.pop("default", None)
        tt._last_activity.pop("default", None)

    assert captured, "the promoted call must still have reached spawn_background_process"
    assert captured.get("timeout") is None, (
        "a promoted foreground call's timeout means 'how long to wait', not 'when to kill'"
    )
