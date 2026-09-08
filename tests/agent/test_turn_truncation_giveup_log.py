"""Regression test for #105771.

When a truncated tool call exhausts its 4 retries, ``_retry_truncated_tool_call``
gave up via ``agent._vprint`` (stdout only) and never reached ``finalize_turn``.
``agent.log`` therefore showed the last tool result, then nothing, and a crashed
turn was indistinguishable from an idle agent. The give-up path must emit an
``agent.conversation_loop`` ERROR record so operators can find it in the log
without reading stdout or the session DB.
"""

import logging
from types import SimpleNamespace

from agent.turn_truncation import _Trunc, _retry_truncated_tool_call


class _StubAgent:
    log_prefix = ""
    session_id = "sess-xyz"

    def __init__(self):
        self._ephemeral_max_output_tokens = 32768

    def _flush_status_buffer(self):
        pass

    def _vprint(self, *a, **k):
        pass

    def _cleanup_task_resources(self, *a, **k):
        pass

    def _persist_session(self, *a, **k):
        pass


def _stub_trunc(agent, *, response, truncated_tool_call_retries=4):
    return _Trunc(
        agent=agent, response=response, finish_reason="length",
        conversation_history=None, api_call_count=1, effective_task_id="task-1",
        current_turn_user_idx=0, messages=[{"role": "user", "content": "hi"}],
        length_continue_retries=0, truncated_response_parts=[],
        truncated_tool_call_retries=truncated_tool_call_retries, retry_count=0,
        compression_attempts=0,
    )


def test_giveup_after_four_retries_logs_error(caplog):
    agent = _StubAgent()
    st = _stub_trunc(agent, response=SimpleNamespace(id="resp-1"))
    with caplog.at_level(logging.ERROR, logger="agent.conversation_loop"):
        verdict = _retry_truncated_tool_call(st, api_kwargs={})
    assert verdict.action == "return"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    msg = errors[0].getMessage()
    assert "4" in msg
    assert "sess-xyz" in msg
    assert "32768" in msg


def test_retry_below_threshold_does_not_give_up_or_log(caplog):
    agent = _StubAgent()
    agent._requested_output_cap_from_api_kwargs = lambda kwargs: None
    agent._buffer_vprint = lambda *a, **k: None
    agent.max_tokens = None
    st = _stub_trunc(agent, response=SimpleNamespace(id="resp-1"), truncated_tool_call_retries=0)
    with caplog.at_level(logging.ERROR, logger="agent.conversation_loop"):
        verdict = _retry_truncated_tool_call(st, api_kwargs={})
    assert verdict.action == "continue"
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
