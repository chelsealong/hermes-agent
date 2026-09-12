"""Admission control for requests sharing a local inference server."""

import contextlib
import threading
from collections.abc import Callable

from agent.model_metadata import _endpoint_host_key, _parse_base_url, is_local_endpoint


_local_endpoint_locks: dict[str, threading.RLock] = {}
_local_endpoint_locks_guard = threading.Lock()


@contextlib.contextmanager
def local_endpoint_lock(agent, base_url: str | None, *, cancelled: Callable[[], bool] | None = None):
    """Keep the backend reserved until the owning request worker exits.

    Queue time is activity, not provider silence. Cancellation must also consult
    the request-local flag because an abandoned worker can outlive its agent turn.
    """
    if not base_url or not is_local_endpoint(base_url):
        yield
        return
    endpoint = _parse_base_url(base_url)
    if endpoint is None or endpoint.scheme not in {"http", "https"}:
        yield
        return
    key = _endpoint_host_key(base_url)
    if key is None:
        yield
        return
    with _local_endpoint_locks_guard:
        lock = _local_endpoint_locks.setdefault(key, threading.RLock())
    acquired = waiting = False
    try:
        if getattr(agent, "_interrupt_requested", False) or (cancelled is not None and cancelled()):
            raise InterruptedError("Agent interrupted while waiting for local backend")
        acquired = lock.acquire(blocking=False)
        if not acquired:
            waiting = True
            agent._emit_wait_notice("Waiting for another request to finish on the local backend.")
            while not acquired:
                if getattr(agent, "_interrupt_requested", False) or (cancelled is not None and cancelled()):
                    raise InterruptedError("Agent interrupted while waiting for local backend")
                agent._touch_activity("waiting for local backend")
                acquired = lock.acquire(timeout=0.5)
        if getattr(agent, "_interrupt_requested", False) or (cancelled is not None and cancelled()):
            raise InterruptedError("Agent interrupted while waiting for local backend")
        if waiting:
            agent._emit_wait_notice("")
            waiting = False
        yield
    finally:
        if acquired:
            lock.release()
        if waiting:
            agent._emit_wait_notice("")
