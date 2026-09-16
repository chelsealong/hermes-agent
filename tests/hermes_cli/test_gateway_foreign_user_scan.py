"""_scan_gateway_pids must not match another OS user's gateway on a shared machine.

See: NousResearch/hermes-agent#112752
"""

import os
from unittest.mock import MagicMock, patch

import pytest

import hermes_cli.gateway as gateway_mod

_GATEWAY_CMD = "python -m hermes_cli.main gateway run"


class TestIsForeignUserProcess:
    def test_same_uid_is_not_foreign(self):
        with (
            patch.object(gateway_mod.os, "getuid", return_value=1000, create=True),
            patch.object(gateway_mod.os, "stat", return_value=MagicMock(st_uid=1000)),
        ):
            assert gateway_mod._is_foreign_user_process(123) is False

    def test_different_uid_is_foreign(self):
        with (
            patch.object(gateway_mod.os, "getuid", return_value=1000, create=True),
            patch.object(gateway_mod.os, "stat", return_value=MagicMock(st_uid=1001)),
        ):
            assert gateway_mod._is_foreign_user_process(123) is True

    def test_opt_out_env_var_disables_the_check(self, monkeypatch):
        monkeypatch.setenv("HERMES_FLEET_SCAN_ALL_USERS", "1")
        with (
            patch.object(gateway_mod.os, "getuid", return_value=1000, create=True),
            patch.object(gateway_mod.os, "stat", return_value=MagicMock(st_uid=1001)),
        ):
            assert gateway_mod._is_foreign_user_process(123) is False

    def test_unreadable_proc_entry_fails_open_without_psutil(self):
        with (
            patch.object(gateway_mod.os, "getuid", return_value=1000, create=True),
            patch.object(gateway_mod.os, "stat", side_effect=OSError("gone")),
            patch.dict("sys.modules", {"psutil": None}),
        ):
            assert gateway_mod._is_foreign_user_process(123) is False


@pytest.mark.linux_only
class TestScanExcludesForeignUser:
    """The process-table scan is the cross-user leak the issue describes; the systemd/
    pidfile paths were already correctly scoped. Foreign-UID filtering must apply
    regardless of ``all_profiles``, since a foreign default-profile gateway is also
    misattributed by the substring-based profile matcher."""

    def _fake_proc(self, entries: dict):
        def _isdir(path):
            return str(path) == "/proc"

        def _listdir(path):
            if str(path) == "/proc":
                return [str(pid) for pid in entries] + ["self"]
            raise FileNotFoundError(path)

        def _open(path, mode="r", **kwargs):
            path_str = str(path)
            if "/cmdline" in path_str:
                pid = int(path_str.split("/proc/")[1].split("/")[0])
                raw = entries.get(pid, "").encode("utf-8").replace(b" ", b"\x00")
                m = MagicMock()
                m.read.return_value = raw
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            raise FileNotFoundError(path)

        return _isdir, _listdir, _open

    def test_foreign_uid_gateway_excluded_even_with_all_profiles(self):
        my_pid = os.getpid()
        own_gateway_pid = 12345
        foreign_gateway_pid = 54321
        entries = {
            my_pid: "python -m hermes_cli.main",
            own_gateway_pid: _GATEWAY_CMD,
            foreign_gateway_pid: _GATEWAY_CMD,
        }
        _isdir, _listdir, _open = self._fake_proc(entries)

        with (
            patch("os.path.isdir", side_effect=_isdir),
            patch("os.listdir", side_effect=_listdir),
            patch("builtins.open", side_effect=_open),
            patch(
                "hermes_cli.gateway._is_foreign_user_process",
                side_effect=lambda pid: pid == foreign_gateway_pid,
            ),
            patch("hermes_cli.gateway._get_ancestor_pids", return_value=set()),
            patch("subprocess.run") as mock_ps,
        ):
            pids = gateway_mod._scan_gateway_pids(set(), all_profiles=True)

        assert own_gateway_pid in pids
        assert foreign_gateway_pid not in pids
        mock_ps.assert_not_called()
