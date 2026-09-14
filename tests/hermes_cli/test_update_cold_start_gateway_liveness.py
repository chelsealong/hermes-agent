"""#84185: a Windows gateway cold-started after update that dies immediately
(e.g. a job object denying breakaway) must not be reported as started.

``_cold_start_windows_gateway_after_update`` used to print the success line
straight off a successful ``Popen`` return, which only proves the process was
created, not that it survived. This asserts the observable output: the
success line is gated on the process actually being found alive afterwards,
same as every other ``_spawn_detached`` caller.
"""

from __future__ import annotations

import os

import pytest

from hermes_cli import gateway as hermes_gateway
from hermes_cli import gateway_windows
from hermes_cli import main as cli_main
import hermes_cli.main_install_repair as main_install_repair
from hermes_cli import process_identity
from hermes_cli import update_cmd
import hermes_cli.update_cmd_windows as update_cmd_windows


def _run_cold_start(monkeypatch, capsys, *, surviving_pids):
    monkeypatch.setattr(cli_main, "_is_windows", lambda: True)
    monkeypatch.setattr(main_install_repair, "_is_windows", lambda: True)

    # The pre-spawn re-check (``all_profiles=True``) must find nothing
    # running so the cold-start path proceeds and actually spawns.
    monkeypatch.setattr(
        hermes_gateway,
        "find_gateway_pids",
        lambda all_profiles=False: [] if all_profiles else surviving_pids,
    )
    # ``_desktop_owns_gateway_lifecycle`` reads the real spawn ledger, keyed off this
    # checkout's on-disk path (``process_identity.install_id``) rather than HERMES_HOME. On a
    # machine where a live gateway shares that path, an unrelated real ledger entry would
    # otherwise satisfy "Desktop owns it" here and short-circuit before ever spawning (#110728).
    monkeypatch.setattr(update_cmd, "_desktop_owns_gateway_lifecycle", lambda: False)
    monkeypatch.setattr(update_cmd_windows, "_desktop_owns_gateway_lifecycle", lambda: False)
    monkeypatch.setattr(gateway_windows, "_spawn_detached", lambda: 4242)
    # Avoid the real 6s/0.4s poll loop in _report_gateway_start.
    monkeypatch.setattr(
        gateway_windows, "_wait_for_gateway_ready", lambda *a, **k: surviving_pids
    )

    update_cmd._cold_start_windows_gateway_after_update()

    return capsys.readouterr().out


def test_cold_start_raises_when_process_does_not_survive(monkeypatch, capsys):
    with pytest.raises(RuntimeError, match="did not become ready"):
        _run_cold_start(monkeypatch, capsys, surviving_pids=[])

    assert "✓ Starting Windows gateway after update" not in capsys.readouterr().out


def test_cold_start_reports_success_when_process_survives(monkeypatch, capsys):
    out = _run_cold_start(monkeypatch, capsys, surviving_pids=[4242])

    assert "✓ Gateway started via cold-start after update" in out


def test_cold_start_ignores_unrelated_live_backend_ledger_entry(monkeypatch, capsys):
    """#110728: an unrelated live "serve" ledger entry for this install path must not silently
    make ``_desktop_owns_gateway_lifecycle`` swallow the cold-start path. Without the isolation
    in ``_run_cold_start``, this same entry would make it report "Desktop owns it" and both
    tests above would see no output / no exception instead of the outcome they assert.
    """
    monkeypatch.setattr(
        process_identity,
        "ledger_entries",
        lambda **_kwargs: [{
            "purpose": "serve",
            "spawner_pid": os.getpid(),
            "spawner_create": process_identity._process_create_time(),
        }],
    )

    out = _run_cold_start(monkeypatch, capsys, surviving_pids=[4242])

    assert "✓ Gateway started via cold-start after update" in out
