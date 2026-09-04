"""Regression test for #102592.

`serve`/`dashboard` are not in main.py's `_AGENT_COMMANDS`, so
`_prepare_agent_startup()` never discovers plugins for these commands. The
only other `discover_plugins()` calls in `web_server.py` are request-time-only
(channels-page rendering, terminal-backend picker), so a process that starts
straight into `start_server()` (the `serve`/`dashboard` entry point) ran every
turn with an empty plugin registry — plugin-registered hooks like
`pre_llm_call` silently never fired.

Uses a non-loopback bind with no auth providers registered, which makes
`start_server()` fail closed with `SystemExit` before it ever reaches uvicorn
— so the test doesn't need to actually bind a socket or run a server loop.
That `SystemExit` is raised well after the fix's `discover_plugins()` call
site, so observing the call proves discovery runs during `start_server()`
startup.
"""
from unittest.mock import patch

import pytest

from hermes_cli import web_server


def test_start_server_discovers_plugins_before_serving():
    from hermes_cli.dashboard_auth import clear_providers

    clear_providers()

    with patch("hermes_cli.plugins.discover_plugins") as mock_discover, \
            patch("hermes_cli.resource_limits.apply_nofile_soft_limit"):
        with pytest.raises(SystemExit):
            web_server.start_server(host="0.0.0.0", open_browser=False)

    mock_discover.assert_called_once_with()
