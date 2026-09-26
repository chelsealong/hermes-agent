"""Messaging-gateway ownership tests for the hosted Group Chat worker."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway import channel_directory, hosted_room_driver, hosted_rooms, run_heartbeat_restore
from gateway.run import GatewayRunner
from tests.gateway.restart_test_helpers import make_restart_runner
from tui_gateway.hosted_room_service import HostedRoomService


class _RPC:
    def __init__(self) -> None:
        self.sessions = {}
        self.submits = []

    def resolve_exact(self, *, profile, title, source):
        del source
        return self.sessions.get((profile, title))

    def create(self, *, profile, title, source):
        del source
        session = {"session_id": f"{profile}-session", "title": title}
        self.sessions[(profile, title)] = session
        return session

    def resume(self, *, profile, session_id, source):
        del profile, source
        return {"session_id": session_id}

    def submit(self, **kwargs):
        self.submits.append(kwargs["profile"])
        kwargs["on_terminal"]({
            "status": "settled",
            "text": f"reply from {kwargs['profile']}",
        })
        return {"accepted": True}

    def history(self, **kwargs):
        del kwargs
        return []

    def info(self, **kwargs):
        del kwargs
        return {"active": False, "task_id": None}

    def interrupt(self, **kwargs):
        del kwargs
        raise AssertionError("gateway lifecycle must not interrupt room work")


def _server():
    return SimpleNamespace(_methods={}, _sessions={}, _sessions_lock=threading.Lock())


def _service(db_path, *, profiles=("default",)):
    service = HostedRoomService(_server(), db_path=db_path)
    rpc = _RPC()
    service.rpc = rpc
    service.runtime.rpc = rpc
    service.local_profiles = lambda: profiles
    return service, rpc


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not settle before timeout")


@pytest.mark.asyncio
async def test_messaging_gateway_supervisor_starts_without_dashboard(monkeypatch):
    from tui_gateway import methods_groups

    state = {"running": False, "starts": 0}

    class Runtime:
        def status(self):
            return {"running": state["running"], "stopping": False}

    service = SimpleNamespace(runtime=Runtime())

    def get_service():
        return service if state["running"] else None

    def start_service():
        state["starts"] += 1
        state["running"] = True
        return service

    monkeypatch.setattr(methods_groups, "get_hosted_room_service", get_service)
    monkeypatch.setattr(methods_groups, "start_hosted_room_service", start_service)

    runner = GatewayRunner.__new__(GatewayRunner)
    started = await runner._ensure_hosted_room_worker()
    assert started is service
    assert state == {"running": True, "starts": 1}

    # A dead child is restarted, while a healthy one is left alone.
    await runner._ensure_hosted_room_worker()
    assert state["starts"] == 1
    state["running"] = False
    await runner._ensure_hosted_room_worker()
    assert state["starts"] == 2


@pytest.mark.asyncio
async def test_dead_room_worker_is_restarted_by_gateway_task_supervision(monkeypatch):
    from tui_gateway import methods_groups

    starts = {"count": 0}

    def fail_start():
        starts["count"] += 1
        raise RuntimeError("worker unavailable")

    monkeypatch.setattr(methods_groups, "get_hosted_room_service", lambda: None)
    monkeypatch.setattr(methods_groups, "start_hosted_room_service", fail_start)
    monkeypatch.setattr(GatewayRunner, "_MAX_SUPERVISED_RESTARTS", 1)
    monkeypatch.setattr(
        GatewayRunner,
        "_supervised_backoff",
        staticmethod(lambda _attempt: 0),
    )

    runner = GatewayRunner.__new__(GatewayRunner)
    runner._running = True
    runner._background_tasks = set()
    runner._spawn_supervised(
        lambda: runner._hosted_room_worker_watcher(interval=0),
        "hosted_room_worker",
    )

    for _ in range(200):
        if starts["count"] == 2 and not runner._background_tasks:
            break
        await asyncio.sleep(0.01)
    runner._running = False

    assert starts["count"] == 2
    assert runner._background_tasks == set()


def test_gateway_restart_resumes_queued_room_for_multiplexed_profile(tmp_path):
    db = tmp_path / "state.db"
    first, _ = _service(db, profiles=("default", "ops"))
    first.create_room(
        room_id="room-1",
        name="Release room",
        members=[
            {
                "member_id": "default",
                "profile": "default",
                "handle": "hermes",
            },
            {"member_id": "ops", "profile": "ops", "handle": "ops"},
        ],
    )
    first.send(
        room_id="room-1",
        event_id="user-1",
        payload={"text": "@ops inspect", "thread_id": "thread-1"},
    )
    assert (
        len(hosted_room_driver.list_tasks(db, room_id="room-1", status="queued")) == 1
    )

    resumed, rpc = _service(db, profiles=("default", "ops"))
    resumed.start()
    try:
        _wait_for(
            lambda: any(
                event["kind"] == "message.member"
                for event in hosted_rooms.read_events(
                    db, room_id="room-1", since_seq=0
                )["events"]
            )
        )
    finally:
        assert resumed.stop(timeout=5.0)

    assert rpc.submits == ["ops"]
    assert hosted_room_driver.list_tasks(db, room_id="room-1", status="settled")


def test_dashboard_and_gateway_workers_share_one_fenced_execution_owner(tmp_path):
    db = tmp_path / "state.db"
    gateway, gateway_rpc = _service(db, profiles=("default", "ops"))
    dashboard, dashboard_rpc = _service(db, profiles=("default", "ops"))
    gateway.create_room(
        room_id="room-1",
        name="Release room",
        members=[
            {
                "member_id": "default",
                "profile": "default",
                "handle": "hermes",
            },
            {"member_id": "ops", "profile": "ops", "handle": "ops"},
        ],
    )
    gateway.send(
        room_id="room-1",
        event_id="user-1",
        payload={"text": "@ops inspect", "thread_id": "thread-1"},
    )

    gateway.start()
    dashboard.start()
    try:
        _wait_for(
            lambda: any(
                event["kind"] == "message.member"
                for event in hosted_rooms.read_events(
                    db, room_id="room-1", since_seq=0
                )["events"]
            )
        )
        time.sleep(0.05)
    finally:
        assert gateway.stop(timeout=5.0)
        assert dashboard.stop(timeout=5.0)

    assert len(gateway_rpc.submits) + len(dashboard_rpc.submits) == 1
    events = hosted_rooms.read_events(db, room_id="room-1", since_seq=0)["events"]
    assert sum(event["kind"] == "message.member" for event in events) == 1


@pytest.mark.asyncio
async def test_hosted_room_worker_start_waits_for_startup_warmup(monkeypatch):
    """#123347: the room worker's own import (``tui_gateway.server`` -> ``tools.connectors``)
    used to start on an executor thread while the startup warm-up's import (``run_agent`` ->
    ``model_tools`` -> ``tools.connectors``) was still running on a SECOND executor thread —
    the warm-up task was only awaited later, in ``_finish_startup_restore``. Two threads racing
    to import overlapping module chains hit CPython's import-lock deadlock detector in
    production. ``_start_post_connect_services`` must await the warm-up first, so the room
    worker's import never starts while the warm-up's is still in flight."""
    monkeypatch.setenv("HERMES_STARTUP_WARMUP_TIMEOUT", "5")
    runner, _adapter = make_restart_runner()
    runner._spawn_supervised = lambda *a, **k: None
    runner._start_loop_heartbeat_task = lambda: None
    runner._start_heartbeat_poller = lambda: None
    runner._send_update_notification = AsyncMock(return_value=True)
    monkeypatch.setattr(run_heartbeat_restore, "restore_heartbeat_watches", AsyncMock())
    monkeypatch.setattr(channel_directory, "build_channel_directory", AsyncMock(return_value={}))

    warmup_done = asyncio.Event()
    runner._startup_warmup_task = asyncio.ensure_future(warmup_done.wait())

    observed = {}

    async def fake_ensure_hosted_room_worker():
        observed["warmup_done"] = runner._startup_warmup_task.done()
        return None

    runner._ensure_hosted_room_worker = fake_ensure_hosted_room_worker

    async def finish_warmup_soon():
        await asyncio.sleep(0.05)
        warmup_done.set()

    finisher = asyncio.ensure_future(finish_warmup_soon())
    try:
        await asyncio.wait_for(runner._start_post_connect_services(connected_count=1), timeout=5)
    finally:
        await finisher

    assert observed["warmup_done"] is True
