"""#120871: a named profile's own standalone gateway must not read as "served by a multiplexer".

``named_profile_served_by_running_multiplexer()``'s host-record rung asked only "does a live host
record serve this profile", not "is that host someone OTHER than me" -- the distinction
``_served_by_another_host_gateway()`` already draws for its own "owner" case. A profile running its
own standalone gateway therefore looked "served", and ``hermes -p <profile> gateway restart``
refused (exit 78) with a bogus "run `hermes -p default gateway restart`" hint, even though no
default gateway existed and restarting the profile's own service was exactly right.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway import host_attach
from gateway import host_rendezvous as hr


def _reset_probe_memo() -> None:
    getattr(host_attach, "invalidate_host_gateway_cache", lambda: None)()


@pytest.fixture(autouse=True)
def _no_memo():
    _reset_probe_memo()
    yield
    _reset_probe_memo()


def _write_record(pid: int, home: Path, profiles: tuple[str, ...]) -> None:
    payload = {
        "role": hr.ROLE_GATEWAY, "home": str(home), "pid": pid,
        "createTime": hr.process_create_time(pid), "host": "", "port": None,
        "protocolVersion": hr.HOST_PROTOCOL_VERSION, "tokenFingerprint": "",
        "profiles": list(profiles), "updatedAt": "",
    }
    path = hr.record_path(hr.ROLE_GATEWAY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def standalone_owner(tmp_path, monkeypatch):
    """A REAL live process published as the host gateway for ITS OWN profile home only -- the
    shape a standalone (non-multiplexing) per-profile gateway publishes, per ``gateway.host_attach``.
    """
    monkeypatch.setenv("HERMES_GATEWAY_LOCK_DIR", str(tmp_path / "locks"))
    root = tmp_path / "hermes"
    home = root / "profiles" / "argus"
    home.mkdir(parents=True)
    (root / "config.yaml").write_text("model:\n  default: x\n", encoding="utf-8")
    (home / "config.yaml").write_text("gateway:\n  standalone: true\n", encoding="utf-8")

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    _write_record(child.pid, home, ("argus",))
    monkeypatch.setattr(
        "gateway.control_socket.identify_gateway",
        lambda dialled, **kw: {"pid": child.pid, "hermes_home": str(home),
                               "served_profiles": ["argus"]})

    import hermes_constants
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(hermes_constants, "_default_hermes_root_memo", None)

    try:
        yield SimpleNamespace(pid=child.pid, root=root, home=home, proc=child)
    finally:
        child.terminate()
        child.wait(timeout=10)


def test_own_standalone_host_record_is_not_served_by_a_multiplexer(standalone_owner):
    from hermes_cli import gateway as gw

    assert gw.named_profile_served_by_running_multiplexer("argus") is False


def test_restart_of_own_standalone_gateway_is_not_refused(standalone_owner, monkeypatch):
    """The end-to-end guard: ``hermes -p argus gateway restart`` must not exit 78 telling the
    operator to manage a nonexistent default gateway instead of restarting Argus itself."""
    from hermes_cli import gateway as gw

    monkeypatch.setattr(gw, "_is_service_installed", lambda: True)

    assert gw._named_profile_refused_under_multiplexer() is False


def test_a_genuine_satellite_of_another_hosts_gateway_is_still_refused(tmp_path, monkeypatch):
    """Control: a DIFFERENT profile's host process really does serve this one -- still refused."""
    monkeypatch.setenv("HERMES_GATEWAY_LOCK_DIR", str(tmp_path / "locks"))
    root = tmp_path / "hermes"
    owner_home = root / "profiles" / "hub"
    served_home = root / "profiles" / "argus"
    owner_home.mkdir(parents=True)
    served_home.mkdir(parents=True)
    (root / "config.yaml").write_text("model:\n  default: x\n", encoding="utf-8")

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    _write_record(child.pid, owner_home, ("hub", "argus"))
    monkeypatch.setattr(
        "gateway.control_socket.identify_gateway",
        lambda dialled, **kw: {"pid": child.pid, "hermes_home": str(owner_home),
                               "served_profiles": ["hub", "argus"]})

    import hermes_constants
    monkeypatch.setenv("HERMES_HOME", str(served_home))
    monkeypatch.setattr(hermes_constants, "_default_hermes_root_memo", None)

    try:
        from hermes_cli import gateway as gw

        assert gw.named_profile_served_by_running_multiplexer("argus") is True
        assert gw._named_profile_refused_under_multiplexer() is True
    finally:
        child.terminate()
        child.wait(timeout=10)
