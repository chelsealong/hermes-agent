"""#122112: a bridged mirror index makes ``uv sync --locked``'s registry check fail
even when uv.lock itself is current — the packages are the same, just fetched from
elsewhere. Retry as ``--frozen`` only when a custom index is actually configured, so
the real staleness check still fires for everyone else."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

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


def test_mirrored_index_lets_locked_sync_finish_against_real_uv(tmp_path, monkeypatch):
    """No fakes: a committed uv.lock always pins pypi.org's canonical index URL
    without a trailing slash. Any UV_DEFAULT_INDEX that resolves to the identical
    index but differs byte-for-byte reproduces #122112 deterministically — a
    trailing slash is enough, so this needs no third-party mirror host and no
    offline/find-links scaffolding. ``--locked`` re-resolves, sees the mismatch,
    and fails even though the installed packages are unchanged; ``--frozen``
    does not re-resolve and installs correctly. Mirrors the real-uv pattern in
    tests/pm/test_venv_sync_ambient_config.py.
    """
    from pm.environment import managed_environment

    uv = shutil.which("uv")
    assert uv, "the isolation contract requires real uv"
    monkeypatch.setattr("pm._uv._toolchain", lambda **kwargs: (Path(uv), Path(sys.executable)))

    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="mirror-fallback-regression"\nversion="1"\nrequires-python=">=3.11"\n'
        'dependencies=["six"]\n[tool.uv]\npackage=false\nexclude-newer="14 days"\n',
        encoding="utf-8",
    )
    managed_environment(tmp_path / "lock-env", offline=False).lock(project)
    assert 'registry = "https://pypi.org/simple"' in (project / "uv.lock").read_text()

    mirror_env = {"UV_DEFAULT_INDEX": "https://pypi.org/simple/"}

    # Negative control: the mirror alone, with no fallback, must reproduce the
    # exact reported failure against real uv.
    raw = managed_environment(tmp_path / "raw", env=mirror_env, offline=False)
    raw.create()
    with pytest.raises(InstallError, match="needs to be updated"):
        raw.sync(project, locked=True, no_default_groups=True,
                 no_install_project=True, timeout=600)

    candidate = managed_environment(tmp_path / "candidate", env=mirror_env, offline=False)
    candidate.create()
    _sync_locked(candidate, project, mirror_env)

    installed = subprocess.run(
        [str(candidate.executable), "-c", "import six; print(six.__version__)"],
        capture_output=True, text=True, timeout=30,
    )
    assert installed.returncode == 0, installed.stderr
