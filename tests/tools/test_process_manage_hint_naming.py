"""Operator-facing hints that tell the agent how to check on a background process must
name a tool that is actually advertised in the schema (``process_manage``), never the
bare ``process`` name — that name is accepted only as a dispatch-time compatibility
alias (``model_tools._LEGACY_TOOL_ALIASES``) for old saved prompts, not something the
model ever sees offered as a callable tool. Regression for issue #124583.
"""

import re

import agent.prompt_builder as prompt_builder
import cron.lifecycle_guard as lifecycle_guard
import tools.bot_mode_dm as bot_mode_dm
import tools.close_terminal_tool as close_terminal_tool
import tools.process_registry  # noqa: F401  (registers "process_manage" on import)
import tools.process_registry_notifications as process_registry_notifications
import tools.terminal_tool as terminal_tool
import tools.terminal_tool_background as terminal_tool_background
from tools.registry import registry

# Matches the bare legacy alias ("process(action=...)" or "process(submit)"/"process(write)"
# with the argument passed positionally), not "process_manage(action=...)".
_BARE_PROCESS_ALIAS_RE = re.compile(r"(?<!_manage)\bprocess\((action=|submit\)|write\))")


def _hint_strings():
    notification = process_registry_notifications.format_process_notification({
        "session_id": "proc_abc123", "command": "echo hi", "exit_code": 0,
        "output": "hi", "output_cut": 50,
    })
    return {
        "terminal_tool.TERMINAL_TOOL_DESCRIPTION": terminal_tool.TERMINAL_TOOL_DESCRIPTION,
        "terminal_tool._PROMOTED_NOTE": terminal_tool._PROMOTED_NOTE,
        "terminal_tool._PROMOTED_NOTE_POLL_ONLY": terminal_tool._PROMOTED_NOTE_POLL_ONLY,
        "terminal_tool._PTY_DISABLED_REASON": terminal_tool._PTY_DISABLED_REASON,
        "terminal_tool_background._SILENT_BACKGROUND_HINT": terminal_tool_background._SILENT_BACKGROUND_HINT,
        "terminal_tool_background._HOMEBREW_CI_POLLER_HINT": terminal_tool_background._HOMEBREW_CI_POLLER_HINT,
        "terminal_tool_background._ASYNC_UNSUPPORTED_NOTE": terminal_tool_background._ASYNC_UNSUPPORTED_NOTE,
        "terminal_tool_background._YIELDED_NOTE": terminal_tool_background._YIELDED_NOTE,
        "close_terminal_tool.CLOSE_TERMINAL_SCHEMA": str(close_terminal_tool.CLOSE_TERMINAL_SCHEMA),
        "bot_mode_dm.message_agent_tool_schema": str(bot_mode_dm.message_agent_tool_schema()),
        "lifecycle_guard.HOST_INTERPRETER_KILL_REJECTION": lifecycle_guard.HOST_INTERPRETER_KILL_REJECTION,
        "process_registry_notifications.format_process_notification": notification,
        "prompt_builder._WINDOWS_BASH_SHELL_HINT": prompt_builder._WINDOWS_BASH_SHELL_HINT,
    }


def test_process_manage_is_the_registered_tool_name():
    assert "process_manage" in registry.get_all_tool_names()


def test_operator_hints_name_process_manage_not_the_bare_alias():
    for label, text in _hint_strings().items():
        match = _BARE_PROCESS_ALIAS_RE.search(text)
        assert match is None, (
            f"{label} tells the operator to call {match.group(0) if match else '<?>'}..., "
            "but no tool named bare 'process' is advertised in any schema (only "
            "'process_manage' is registered) — use process_manage(action=...) instead."
        )
