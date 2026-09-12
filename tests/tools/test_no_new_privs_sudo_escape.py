"""Tests for the Electron NO_NEW_PRIVS sudo escape (LocalEnvironment, Linux only).

Electron/Chromium sets the kernel's one-way PR_SET_NO_NEW_PRIVS latch on itself at
startup; every child the Desktop app spawns inherits it, so ``sudo`` can never regain
its setuid privilege — it fails with "a password is required" / "no tty present"
regardless of configuration. Re-launching the sudo-bearing command through this user's
``systemd-run --user`` unit (started BY the manager, not forked from our latched tree)
recovers sudo. See https://github.com/NousResearch/hermes-agent/issues/108595.
"""

from unittest.mock import MagicMock, patch

import pytest

from tools.environments.local import LocalEnvironment
from tools.terminal_tool_sudo import (
    _no_new_privs_escape_runtime_paths,
    _no_new_privs_set,
    _systemd_user_runtime_paths,
    _wrap_argv_for_no_new_privs,
)

# The whole feature is Linux-only (PR_SET_NO_NEW_PRIVS is a Linux prctl(); the
# systemd --user manager it escapes into doesn't exist on macOS/Windows).
pytestmark = pytest.mark.linux_only


class TestNoNewPrivsSet:
    def test_flag_set(self, tmp_path):
        status = tmp_path / "status"
        status.write_text("Name:\tbash\nNoNewPrivs:\t1\nSeccomp:\t0\n", encoding="utf-8")
        assert _no_new_privs_set(str(status)) is True

    def test_flag_clear(self, tmp_path):
        status = tmp_path / "status"
        status.write_text("Name:\tbash\nNoNewPrivs:\t0\nSeccomp:\t0\n", encoding="utf-8")
        assert _no_new_privs_set(str(status)) is False

    def test_missing_file_is_not_set(self, tmp_path):
        assert _no_new_privs_set(str(tmp_path / "does-not-exist")) is False


class TestEscapeRuntimePaths:
    def test_unavailable_without_systemd_run_on_path(self):
        with patch("shutil.which", return_value=None):
            assert _no_new_privs_escape_runtime_paths(1000) is None

    def test_unavailable_when_runtime_dir_missing(self, tmp_path):
        missing_dir = tmp_path / "no-such-runtime-dir"
        with patch("shutil.which", return_value="/usr/bin/systemd-run"), \
             patch("tools.terminal_tool_sudo._systemd_user_runtime_paths",
                   return_value=(str(missing_dir), f"unix:path={missing_dir}/bus")):
            assert _no_new_privs_escape_runtime_paths(1000) is None

    def test_unavailable_when_bus_socket_missing(self, tmp_path):
        runtime_dir = tmp_path / "run-user-1000"
        runtime_dir.mkdir()
        with patch("shutil.which", return_value="/usr/bin/systemd-run"), \
             patch("tools.terminal_tool_sudo._systemd_user_runtime_paths",
                   return_value=(str(runtime_dir), f"unix:path={runtime_dir}/bus")):
            assert _no_new_privs_escape_runtime_paths(1000) is None

    def test_available_when_runtime_dir_and_bus_both_present(self, tmp_path):
        runtime_dir = tmp_path / "run-user-1000"
        runtime_dir.mkdir()
        (runtime_dir / "bus").write_text("", encoding="utf-8")
        with patch("shutil.which", return_value="/usr/bin/systemd-run"), \
             patch("tools.terminal_tool_sudo._systemd_user_runtime_paths",
                   return_value=(str(runtime_dir), f"unix:path={runtime_dir}/bus")):
            result = _no_new_privs_escape_runtime_paths(1000)
        assert result == (str(runtime_dir), f"unix:path={runtime_dir}/bus")

    def test_derivation_matches_systemd_convention(self):
        runtime_dir, bus_address = _systemd_user_runtime_paths(1000)
        assert runtime_dir == "/run/user/1000"
        assert bus_address == "unix:path=/run/user/1000/bus"


class TestWrapArgvForNoNewPrivs:
    def test_wraps_with_pipe_wait_and_same_dir(self):
        argv = _wrap_argv_for_no_new_privs(["bash", "-c", "sudo -S -p '' apt update"], {})
        assert argv[0] == "systemd-run"
        for flag in ("--user", "--pipe", "--wait", "--quiet", "--collect", "--same-dir"):
            assert flag in argv
        assert argv[-4:] == ["--", "bash", "-c", "sudo -S -p '' apt update"]

    def test_forwards_run_env_via_setenv_excluding_manager_owned_keys(self):
        run_env = {
            "VIRTUAL_ENV": "/opt/venv", "PYTHONPATH": "/opt/venv/lib",
            "XDG_RUNTIME_DIR": "/some/electron/private/path",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/some/electron/private/bus",
        }
        argv = _wrap_argv_for_no_new_privs(["bash", "-c", "sudo true"], run_env)
        assert "--setenv=VIRTUAL_ENV=/opt/venv" in argv
        assert "--setenv=PYTHONPATH=/opt/venv/lib" in argv
        # The manager supplies its own correct XDG_RUNTIME_DIR/DBUS_SESSION_BUS_ADDRESS;
        # forwarding Electron's wrong values via --setenv would override that.
        assert not any(a.startswith("--setenv=XDG_RUNTIME_DIR=") for a in argv)
        assert not any(a.startswith("--setenv=DBUS_SESSION_BUS_ADDRESS=") for a in argv)

    def test_sets_runtime_max_sec_from_timeout(self):
        argv = _wrap_argv_for_no_new_privs(["bash", "-c", "sudo true"], {}, timeout=90)
        assert "--property=RuntimeMaxSec=90s" in argv

    def test_no_runtime_max_sec_without_a_positive_timeout(self):
        for bad_timeout in (None, 0, -1, "not-a-number"):
            argv = _wrap_argv_for_no_new_privs(["bash", "-c", "sudo true"], {}, timeout=bad_timeout)
            assert not any(a.startswith("--property=RuntimeMaxSec=") for a in argv)


def _make_fake_popen(captured: dict):
    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env", {})
        proc = MagicMock()
        proc.poll.return_value = 0
        proc.returncode = 0
        proc.stdout = MagicMock(__iter__=lambda s: iter([]), __next__=lambda s: (_ for _ in ()).throw(StopIteration))
        proc.stdin = MagicMock()
        return proc
    return fake_popen


class TestLocalEnvironmentEscapeIntegration:
    """LocalEnvironment._run_bash is the only call site: it is the single funnel through
    which every local sudo-bearing command passes (real execution AND the NOPASSWD
    probe), so gating there covers both without touching Docker/SSH/other backends."""

    def _run(self, command: str, *, no_new_privs: bool, escape_available: bool, timeout: int = 45):
        captured = {}
        runtime_paths = ("/run/user/1000", "unix:path=/run/user/1000/bus") if escape_available else None
        with patch("tools.environments.local._find_bash", return_value="/bin/bash"), \
             patch("tools.environments.local._IS_LINUX", True), \
             patch("tools.environments.local._IS_WINDOWS", False), \
             patch("subprocess.Popen", side_effect=_make_fake_popen(captured)), \
             patch("tools.terminal_tool_sudo._no_new_privs_set", return_value=no_new_privs), \
             patch("tools.terminal_tool_sudo._no_new_privs_escape_runtime_paths", return_value=runtime_paths), \
             patch("os.getuid", return_value=1000, create=True):  # windows-footgun: ok — module is pytest.mark.linux_only
            env = LocalEnvironment(cwd="/tmp", timeout=timeout)
            env.execute(command)
        return captured

    def test_sudo_command_wrapped_when_flag_set_and_escape_available(self):
        captured = self._run("sudo apt update", no_new_privs=True, escape_available=True)
        assert captured["cmd"][0] == "systemd-run"
        assert "--" in captured["cmd"]
        assert captured["cmd"][-1] == captured["cmd"][-1]  # sanity: cmd non-empty
        # Manager-owned identity vars in the Popen env are OUR derived, correct values —
        # not whatever Electron happened to leave in os.environ.
        assert captured["env"]["XDG_RUNTIME_DIR"] == "/run/user/1000"
        assert captured["env"]["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"

    def test_non_sudo_command_never_wrapped_even_with_flag_set(self):
        captured = self._run("echo hello", no_new_privs=True, escape_available=True)
        assert captured["cmd"][0] != "systemd-run"

    def test_sudo_command_not_wrapped_when_flag_clear(self):
        """The common case (CLI, gateway, non-Desktop): NO_NEW_PRIVS is 0, sudo already
        works, and the command must run exactly as it always has."""
        captured = self._run("sudo apt update", no_new_privs=False, escape_available=True)
        assert captured["cmd"][0] != "systemd-run"

    def test_sudo_command_not_wrapped_when_escape_unavailable(self):
        """Fail closed: no systemd-run on PATH, or no reachable user manager — run
        unwrapped rather than risk a systemd-run invocation that cannot connect. Behavior
        matches today (sudo fails with its usual message), never worse."""
        captured = self._run("sudo apt update", no_new_privs=True, escape_available=False)
        assert captured["cmd"][0] != "systemd-run"

    def test_wrapped_runtime_max_sec_matches_command_timeout(self):
        captured = self._run("sudo apt update", no_new_privs=True, escape_available=True, timeout=45)
        assert "--property=RuntimeMaxSec=45s" in captured["cmd"]
