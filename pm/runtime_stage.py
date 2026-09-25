"""One locked dependency builder for PM workers and packaged runtimes."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from pm.package import InstallError


def _sync_locked(environment, snapshot: Path, env: Mapping[str, str]) -> None:
    """``--locked`` also asserts uv.lock's recorded registry still matches what's
    resolving today. A bridged or explicit mirror (pm.index_config) makes that
    assertion fail even when the lock itself is current — the packages are the
    same, just fetched from elsewhere (#122112). Retry with ``--frozen`` (trust
    the lock, skip that check) only when a non-default index is actually in play,
    so the staleness check still fires for everyone else.

    Blind spot: uv raises the identical "needs to be updated" error for a
    registry-only mismatch AND for a genuinely stale lock (e.g. a dependency
    added to pyproject.toml without re-running ``uv lock``), so the ``--frozen``
    retry can't tell them apart and would silently install a venv missing the
    new dependency in the second case. This is out of reach for the one caller
    wired up today (``stage_runtime``, against ``pm/pyproject.toml`` +
    ``pm/uv.lock``): every current caller (``scripts/bundles/native.py``,
    ``scripts/termux/build_environment.py``, ``pm/runtime.py::prepare_runtime``)
    builds that same lock in a mirror-free release/CI environment first, so a
    genuinely stale ``pm/uv.lock`` already fails loudly there before any
    mirrored end-user machine reaches this fallback. That invariant breaks if a
    future caller passes a user-supplied ``project`` whose lock was never
    verified mirror-free — re-check this reasoning before adding one.
    """
    from pm.index_config import has_custom_index

    try:
        environment.sync(snapshot, locked=True, no_default_groups=True,
                         no_install_project=True, timeout=600)
    except InstallError:
        if not has_custom_index(env):
            raise
        environment.sync(snapshot, locked=False, no_default_groups=True,
                         no_install_project=True, timeout=600)


def stage_runtime(uv: Path, python: Path, destination: Path, *,
                  project: Path | None = None, offline: bool = False,
                  wheelhouse: Path | None = None, cache: Path | None = None) -> Path:
    """Build at the final path; the caller owns publication and its marker.

    The scratch project prevents uv from discovering the application's workspace.
    No project install, application extra, or application lock enters this graph.
    """
    from pm.environment import PythonEnvironment
    from pm.packages import uv_cache_dir
    from pm.runtime import runtime_environment

    project = project or Path(__file__).resolve().parent
    destination = destination.absolute()
    env = runtime_environment()
    environment = PythonEnvironment(
        uv=uv, python=python, destination=destination,
        cache=uv_cache_dir() if cache is None else cache.absolute(), env=env,
        offline=offline or wheelhouse is not None, output=sys.stderr, no_config=True,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pm-project-", dir=destination.parent) as temp:
        snapshot = Path(temp)
        for name in ("pyproject.toml", "uv.lock"):
            shutil.copyfile(project / name, snapshot / name)
        environment.create()
        if wheelhouse is None:
            _sync_locked(environment, snapshot, env)
        else:
            environment.install_wheelhouse(snapshot, wheelhouse, timeout=600)
    checked = subprocess.run(
        [str(environment.executable), "-I", "-B", "-c",
         "import packaging, tomli_w, truststore; from ruamel.yaml import YAML"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    if checked.returncode:
        raise InstallError("pm-runtime", f"dependency validation failed: {checked.stderr.strip()}")
    return environment.executable
