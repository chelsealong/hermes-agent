"""Regression for #126004: a failed/timed-out ``git worktree add`` in the
kanban dispatch path must not leave a partial target directory behind.

Git writes the linked worktree's ``.git`` pointer file before materializing
any content, so an uncleaned partial target satisfies
``_ensure_git_worktree``'s own "already a matching worktree" fast path on the
next call — the retry silently reuses the incomplete checkout instead of
redoing the add. The fix sweeps the target with the existing
``_cleanup_failed_worktree_add`` helper (already used by the ``hermes -w``
self-heal path) before raising, so nothing survives for a retry to reuse.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hermes_cli import kanban_db_workspace as kbw


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        [
            "git", "-C", str(cwd),
            "-c", "user.name=Test User",
            "-c", "user.email=test@example.com",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        check=True, capture_output=True, text=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True, capture_output=True)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "init")
    return root


def test_failed_add_does_not_leave_a_reusable_partial_target(repo: Path, tmp_path: Path):
    """A pre-existing non-empty target (stand-in for a killed prior attempt's
    leftovers) makes ``git worktree add`` fail. Without cleanup the directory
    survives and a retry would silently accept it as done; with the fix the
    target is gone, so nothing is left to reuse.

    The target lives outside ``repo`` so ``_ensure_git_worktree``'s own
    "already a matching worktree" fast path (which resolves the target's git
    common-dir) cannot short-circuit before the ``add`` is even attempted.
    """
    target = tmp_path / "external_target"
    target.mkdir(parents=True)
    (target / "stray.txt").write_text("leftover from a killed checkout\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="git worktree add failed"):
        kbw._ensure_git_worktree(repo, target, "wt/t_1")

    assert not target.exists(), "partial target directory must not survive a failed add"


def test_retry_succeeds_after_a_failed_add(repo: Path, tmp_path: Path):
    """The whole point: once the target is swept, the identical call succeeds."""
    target = tmp_path / "external_target"
    target.mkdir(parents=True)
    (target / "stray.txt").write_text("leftover\n", encoding="utf-8")

    with pytest.raises(RuntimeError):
        kbw._ensure_git_worktree(repo, target, "wt/t_2")

    kbw._ensure_git_worktree(repo, target, "wt/t_2")

    assert target.is_dir()
    assert (target / "README.md").exists(), "retry must produce a real checkout"
