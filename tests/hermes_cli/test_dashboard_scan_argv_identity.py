"""Dashboard/serve process scan must identify by token, never argv substring (#121156).

A substring test on ``hermes serve`` also matches any unrelated process whose argv merely
mentions both words -- e.g. a terminal multiplexer session named ``hermes`` running its
``server`` subcommand. ``hermes update`` / ``hermes dashboard --stop`` feed the scan result
straight into a SIGTERM (and, for a systemd-owned PID, a unit restart), so a false positive
kills and restarts an unrelated process.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import hermes_cli.dashboard_procs as dp


def test_is_dashboard_or_serve_cmdline_rejects_lookalike_argv():
    """``hermes server`` (and other words that merely contain ``serve``) are not the ``serve``
    subcommand; only a real Hermes entry point followed by the exact token qualifies."""
    assert not dp._is_dashboard_or_serve_cmdline("tool --name hermes server 30")
    assert not dp._is_dashboard_or_serve_cmdline("herdr --session hermes server")
    assert not dp._is_dashboard_or_serve_cmdline("hermes kanban --preserve-cache")
    assert dp._is_dashboard_or_serve_cmdline("hermes serve --host 127.0.0.1 --port 9119")
    assert dp._is_dashboard_or_serve_cmdline(
        "/opt/hermes/bin/python -m hermes_cli.main dashboard --port 9119"
    )


def test_scan_dashboard_processes_spares_lookalike_decoy(monkeypatch):
    """The end-to-end scan (as ``hermes update`` / ``--stop`` consume it) must not surface the
    decoy PID from the issue's own reproduction, while a real backend is still found."""
    fake_pi = SimpleNamespace(ledger_entries=lambda **k: [])
    monkeypatch.setitem(sys.modules, "hermes_cli.process_identity", fake_pi)
    ps_stdout = (
        "22222 tool --name hermes server 30\n"
        "11111 hermes serve --host 127.0.0.1 --port 9119\n"
    )
    with patch.object(sys, "platform", "linux"):
        monkeypatch.setattr(
            dp.subprocess, "run",
            lambda *a, **k: SimpleNamespace(returncode=0, stdout=ps_stdout),
        )
        result = dp._scan_dashboard_processes()
    pids = [pid for pid, _cmd in result]
    assert 11111 in pids
    assert 22222 not in pids
