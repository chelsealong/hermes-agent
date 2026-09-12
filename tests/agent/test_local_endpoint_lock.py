"""Tests for local_endpoint_lock (#108596): concurrent requests against the same
local/self-hosted backend must queue instead of contending for the same GPU slot.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
import time
from types import SimpleNamespace

import pytest

from agent.local_endpoint_queue import local_endpoint_lock


def _stub_agent(base_url):
    notices = []
    return SimpleNamespace(
        base_url=base_url,
        _interrupt_requested=False,
        _emit_wait_notice=lambda text: notices.append(text),
        _touch_activity=lambda desc: None,
    ), notices


class TestLocalEndpointLock:
    @pytest.mark.parametrize("urls", [
        ("http://localhost:11434/v1", "http://localhost:11434/v1"),
        ("http://LOCALHOST/v1", "http://localhost:80/router/v1"),
        ("https://192.168.1.10/v1", "https://192.168.1.10:443/other"),
    ])
    @pytest.mark.parametrize("provider_failure", [False, True])
    def test_cross_thread_contention_serializes(self, urls, provider_failure):
        """One host and effective port share admission, even when a request fails."""
        agent, _ = _stub_agent(urls[0])
        held, arrived, release = threading.Event(), threading.Event(), threading.Event()
        agent._emit_wait_notice = lambda text: arrived.set() if text else None
        active = {"count": 0, "max_seen": 0}
        active_lock = threading.Lock()
        errors = []

        def request(first, url):
            try:
                with local_endpoint_lock(agent, url):
                    with active_lock:
                        active["count"] += 1
                        active["max_seen"] = max(active["max_seen"], active["count"])
                    try:
                        if first:
                            held.set()
                            assert release.wait(timeout=5)
                            if provider_failure:
                                raise ValueError("provider failure")
                        else:
                            arrived.set()
                    finally:
                        with active_lock:
                            active["count"] -= 1
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=request, args=(True, urls[0]))
        t2 = threading.Thread(target=request, args=(False, urls[1]))
        t1.start()
        try:
            assert held.wait(timeout=5)
            t2.start()
            assert arrived.wait(timeout=5)
        finally:
            release.set()
            t1.join(timeout=5)
            if t2.ident is not None:
                t2.join(timeout=5)
        assert not t1.is_alive() and not t2.is_alive()
        assert [type(exc) for exc in errors] == ([ValueError] if provider_failure else [])
        assert active["max_seen"] == 1

    def test_losing_thread_gets_wait_notice_and_clears_it(self):
        agent, notices = _stub_agent("http://127.0.0.1:8080")
        held, release = threading.Event(), threading.Event()

        def hold():
            with local_endpoint_lock(agent, agent.base_url):
                held.set()
                assert release.wait(timeout=5)

        def notice(text):
            notices.append(text)
            if text:
                release.set()

        agent._emit_wait_notice = notice
        t1 = threading.Thread(target=hold)
        t1.start()
        try:
            assert held.wait(timeout=5)
            with local_endpoint_lock(agent, agent.base_url):
                pass
        finally:
            release.set()
            t1.join(timeout=5)

        assert any("Waiting for another request" in n for n in notices)
        assert notices[-1] == ""

    def test_same_thread_reentry_does_not_deadlock(self):
        """Codex streaming re-enters the non-streaming entry point on the SAME
        thread; a plain Lock would self-deadlock here. RLock must not block and
        must not fire a wait notice for the nested acquire."""
        agent, notices = _stub_agent("http://localhost:11434")

        done = {"ok": False}

        def nested():
            with local_endpoint_lock(agent, agent.base_url):
                with local_endpoint_lock(agent, agent.base_url):
                    done["ok"] = True

        t = threading.Thread(target=nested)
        t.start()
        t.join(timeout=2)

        assert done["ok"] is True
        assert notices == []

    @pytest.mark.parametrize("urls", [
        ("https://api.openai.com/v1", "https://api.openai.com/v1"),
        ("moa://local", "moa://local"),
        ("http://127.0.0.1:1234/v1", "http://127.0.0.1:1235/v1"),
    ])
    def test_independent_endpoints_never_serialize(self, urls):
        """Cloud backends must not be gated at all — two concurrent calls run
        with no coordination."""
        agent, _ = _stub_agent(urls[0])
        active = {"count": 0, "max_seen": 0}
        active_lock = threading.Lock()
        barrier = threading.Barrier(2, timeout=5)

        def hold(url):
            with local_endpoint_lock(agent, url):
                with active_lock:
                    active["count"] += 1
                    active["max_seen"] = max(active["max_seen"], active["count"])
                try:
                    barrier.wait()
                finally:
                    with active_lock:
                        active["count"] -= 1

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(hold, url) for url in urls]
            for future in futures:
                future.result(timeout=10)

        assert active["max_seen"] == 2, "non-local calls were unexpectedly serialized"

    def test_interrupt_while_waiting_raises(self):
        """A request queued behind the lock must abort promptly when the agent
        is interrupted, instead of waiting out the holder indefinitely."""
        agent, _ = _stub_agent("http://localhost:11434")

        def hold():
            with local_endpoint_lock(agent, agent.base_url):
                time.sleep(2.0)

        t = threading.Thread(target=hold)
        t.start()
        time.sleep(0.05)
        agent._interrupt_requested = True
        with pytest.raises(InterruptedError):
            with local_endpoint_lock(agent, agent.base_url):
                pass
        agent._interrupt_requested = False
        t.join(timeout=5)

    def test_interrupt_while_waiting_clears_wait_notice(self):
        """An interrupted waiter clears the status line it actually displayed."""
        agent, notices = _stub_agent("http://localhost:11434")
        held, release = threading.Event(), threading.Event()

        def hold():
            with local_endpoint_lock(agent, agent.base_url):
                held.set()
                assert release.wait(timeout=5)

        def interrupt_after_notice(text):
            notices.append(text)
            if text:
                agent._interrupt_requested = True

        agent._emit_wait_notice = interrupt_after_notice
        t = threading.Thread(target=hold)
        t.start()
        try:
            assert held.wait(timeout=5)
            with pytest.raises(InterruptedError):
                with local_endpoint_lock(agent, agent.base_url):
                    pass
        finally:
            agent._interrupt_requested = False
            release.set()
            t.join(timeout=5)

        assert notices[-1] == "", "wait notice was left stale after interrupt"
