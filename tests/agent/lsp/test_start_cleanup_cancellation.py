"""Regression for #116065: a language-server process leaked forever when
spawn/initialize failed and the caller's outer timeout then cancelled the
failure-cleanup coroutine before it could escalate from SIGTERM to SIGKILL.

``LSPService`` runs every client call through ``_BackgroundLoop.run(coro,
timeout=...)`` (agent/lsp/manager.py), which cancels the in-flight coroutine
on timeout. If that coroutine is inside ``start()``'s except block awaiting
``_cleanup_process()``, cancellation used to abort the SIGTERM->SIGKILL
escalation mid-flight, leaving a SIGTERM-ignoring server (e.g.
kotlin-language-server) running indefinitely.
"""
from __future__ import annotations

import asyncio
import sys

import pytest

from agent.lsp import client as client_module
from agent.lsp.client import LSPClient, LSPProtocolError

# How long we let the shielded cleanup run after cancelling the caller before
# concluding the process was never killed. Generous margin over the patched
# SHUTDOWN_GRACE below so this isn't a race on a loaded CI box.
_CLEANUP_WAIT = 5.0


def _make_client() -> LSPClient:
    return LSPClient(server_id="cancel-test", workspace_root=".", command=["true"])


async def _spawn_ignoring_sigterm() -> "asyncio.subprocess.Process":
    """A real subprocess that ignores SIGTERM, so only SIGKILL ends it.

    Blocks until the child confirms the handler is installed (prints a ready
    line) so terminate() below can never race the child's own startup and
    land while SIGTERM still has its default (fatal) disposition.
    """
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(30)",
        start_new_session=True,
        stdout=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None
    await proc.stdout.readline()
    return proc


async def _has_exited(proc: "asyncio.subprocess.Process", timeout: float) -> bool:
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


@pytest.mark.linux_only
@pytest.mark.asyncio
async def test_start_failure_cleanup_survives_outer_cancellation(monkeypatch):
    monkeypatch.setattr(client_module, "SHUTDOWN_GRACE", 3.0)
    client = _make_client()
    spawned = asyncio.Event()
    proc_holder: dict = {}

    async def _fake_spawn():
        proc_holder["proc"] = await _spawn_ignoring_sigterm()
        client._proc = proc_holder["proc"]
        spawned.set()

    async def _fake_initialize():
        raise LSPProtocolError("boom")

    monkeypatch.setattr(client, "_spawn", _fake_spawn)
    monkeypatch.setattr(client, "_initialize", _fake_initialize)

    task = asyncio.ensure_future(client.start())
    await spawned.wait()
    # Let the failure path reach terminate() and settle into its post-SIGTERM wait.
    await asyncio.sleep(0.5)
    assert not task.done()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    proc = proc_holder["proc"]
    try:
        assert await _has_exited(proc, timeout=_CLEANUP_WAIT), (
            "process ignoring SIGTERM survived: cancelling the caller cut the "
            "SIGTERM->SIGKILL escalation short"
        )
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
