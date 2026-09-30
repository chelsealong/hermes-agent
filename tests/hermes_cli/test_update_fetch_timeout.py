"""A dead-stalled network fetch ends `hermes update` with an error, never a hang (#93759, #95777, #129138).

`_git_run(network=True)` bounds the wait and tree-kills the child on timeout; the timeout becomes a
failed CompletedProcess whose stderr names the stall, so every caller's existing fetch-failure path
prints one clear line. A grandchild that inherited the captured pipes (git-remote-https) must not
wedge the cleanup. Local git (network=False) is unbounded.
"""

import os
import subprocess
import sys
import time
from unittest.mock import MagicMock

import pytest

import hermes_cli.update_cmd as update_cmd

# A "git" whose child inherits the pipes and outlives it, like git.exe -> git-remote-https.exe.
_HUNG_TREE = [sys.executable, "-c",
              "import subprocess,sys,time;"
              "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
              "open(sys.argv[2],'w').write(str(c.pid));time.sleep(60)"]


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(update_cmd, "_m", lambda: MagicMock(PROJECT_ROOT=tmp_path))


def test_stalled_fetch_kills_the_pipe_holding_grandchild(monkeypatch, tmp_path):
    monkeypatch.setattr(update_cmd, "NETWORK_GIT_TIMEOUT_SECONDS", 2)
    pidfile = tmp_path / "grandchild.pid"
    start = time.monotonic()
    result = update_cmd._git_run(_HUNG_TREE[:1], _HUNG_TREE[1:] + ["fetch", str(pidfile)], network=True)

    assert time.monotonic() - start < 30
    assert result.returncode == 124
    assert "timed out" in result.stderr
    grandchild = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except OSError:
            break
        time.sleep(0.1)
    else:
        os.kill(grandchild, 9)
        pytest.fail("the timed-out fetch's grandchild survived holding the pipes")


def test_timeout_with_check_raises_and_local_git_is_unbounded(monkeypatch, tmp_path):
    monkeypatch.setattr(update_cmd, "NETWORK_GIT_TIMEOUT_SECONDS", 1)
    with pytest.raises(subprocess.CalledProcessError) as exc:
        update_cmd._git_run(_HUNG_TREE[:1], _HUNG_TREE[1:] + ["fetch", str(tmp_path / "p")], network=True, check=True)
    assert exc.value.returncode == 124

    ok = update_cmd._git_run([sys.executable], ["-c", "import time;time.sleep(1.5);print('ok')"])
    assert ok.returncode == 0 and ok.stdout.strip() == "ok"
