"""A spawn-boundary infrastructure failure must not consume the card's retry budget."""
from __future__ import annotations

import json

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd

SCOPE_ERROR = (
    "cannot create restart-safe systemd scope for gateway child: systemd-run --user "
    "--scope is unavailable (usually no reachable user D-Bus session at "
    "/run/user/1000/bus). On a system-level service install, run "
    "`sudo loginctl enable-linger <gateway-user>` and restart the gateway."
)


@pytest.fixture
def isolated_board(tmp_path, monkeypatch):
    """A private board, never the developer's own."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    kb.init_db()
    resolved = kb.kanban_db_path().resolve()
    assert resolved.is_relative_to(tmp_path.resolve()), f"refusing to touch {resolved}"


def test_spawn_scope_failure_keeps_the_card_retryable(
    isolated_board, all_assignees_spawnable,
):
    """Three host-level spawn refusals > failure_limit must not park the card."""
    attempts: list[str] = []

    def raising_spawn(task, workspace, board=None):
        # Stand-in for _default_spawn when the user bus is unreachable: the exact
        # exception _restart_safe_worker_argv() propagates in production.
        attempts.append(task.id)
        raise RuntimeError(SCOPE_ERROR)

    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="worker cannot spawn", assignee="builder")
        for _ in range(3):  # more ticks than failure_limit (2)
            kbd.dispatch_once(
                conn, spawn_fn=raising_spawn, failure_limit=2, reconcile_orphans=False,
            )
        row = conn.execute(
            "SELECT status, block_kind, consecutive_failures FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        runs = conn.execute(
            "SELECT outcome, metadata FROM task_runs WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()

    # The card did nothing wrong: nothing counted, no permanent park.
    assert row["consecutive_failures"] == 0, (
        "an infrastructure failure consumed the retry budget "
        f"(consecutive_failures={row['consecutive_failures']})"
    )
    assert row["status"] == "ready", (
        f"card parked as {row['status']!r} (block_kind={row['block_kind']!r}) instead of "
        "staying retryable once the host recovers"
    )
    assert len(attempts) == 3, "a parked card is never attempted again — every tick must retry"
    assert not [r for r in runs if r["outcome"] == "gave_up"], (
        "the circuit breaker tripped on a host failure"
    )
    # Every closed run carries the structured infrastructure self-report.
    for run in runs:
        metadata = json.loads(run["metadata"] or "{}")
        assert metadata.get("infrastructure") is True, (
            f"run outcome={run['outcome']!r} does not mark the failure infrastructural: {metadata}"
        )
