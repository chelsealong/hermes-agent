"""#122112: a bridged mirror index makes ``uv sync --locked``'s registry check fail
even when uv.lock itself is current — the packages are the same, just fetched from
elsewhere. Retry as ``--frozen`` only when a custom index is actually configured, so
the real staleness check still fires for everyone else."""
from __future__ import annotations

import pytest

from pm.package import InstallError
from pm.runtime_stage import _sync_locked


class _RecordingEnvironment:
    def __init__(self, fail_when_locked: bool):
        self.fail_when_locked = fail_when_locked
        self.calls: list[dict] = []

    def sync(self, source, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("locked") and self.fail_when_locked:
            raise InstallError("venv", "uv sync exited 1: error: The lockfile at `uv.lock` "
                                       "needs to be updated, but `--locked` was provided.")


def test_mirrored_index_falls_back_to_frozen_after_locked_fails(tmp_path):
    environment = _RecordingEnvironment(fail_when_locked=True)

    _sync_locked(environment, tmp_path, {"UV_INDEX_URL": "https://mirror.example/simple"})

    assert [call["locked"] for call in environment.calls] == [True, False]


def test_no_custom_index_lets_the_locked_failure_propagate(tmp_path):
    environment = _RecordingEnvironment(fail_when_locked=True)

    with pytest.raises(InstallError, match="needs to be updated"):
        _sync_locked(environment, tmp_path, {})

    # No retry: without a mirror, a --locked failure means the lock is genuinely stale.
    assert [call["locked"] for call in environment.calls] == [True]


def test_locked_success_needs_no_retry(tmp_path):
    environment = _RecordingEnvironment(fail_when_locked=False)

    _sync_locked(environment, tmp_path, {"UV_INDEX_URL": "https://mirror.example/simple"})

    assert [call["locked"] for call in environment.calls] == [True]
