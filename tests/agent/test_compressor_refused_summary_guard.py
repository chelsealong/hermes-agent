"""A model refusal must never be committed as a compaction summary.

#118363: the validator accepted a non-empty, non-truncated response whose entire
body was a refusal ("I can't produce this summary as requested...") — it passes
every existing check (finish_reason=stop, real content) and would otherwise be
stored as the compaction checkpoint, silently discarding the compacted turns
with no summary content at all.
"""

from unittest.mock import MagicMock, patch

from agent.context_compressor import ContextCompressor

_REFUSAL_TEXT = (
    "I can't produce this summary as requested. Two parts of the instruction "
    "conflict with how I need to operate, and one part isn't something I can do "
    "at all."
)


def _mock_response(content="a perfectly fine summary", finish_reason="stop"):
    resp = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish_reason
    resp.choices = [choice]
    return resp


def _msgs(n=12):
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i} " + "x" * 50}
        for i in range(n)
    ]


class TestGenerateSummaryRefusalGuard:
    def test_refusal_is_rejected_and_aborts(self):
        """A refused summary must not become a checkpoint; with no distinct aux model
        to fall back from, compression ABORTS and the session is preserved unchanged."""
        with patch("agent.context_compressor.get_model_context_length", return_value=100000):
            c = ContextCompressor(
                model="test", quiet_mode=True,
                protect_first_n=2, protect_last_n=2,
                abort_on_summary_failure=False,
            )
        msgs = _msgs()
        with patch(
            "agent.context_compressor.call_llm",
            return_value=_mock_response(_REFUSAL_TEXT, "stop"),
        ):
            result = c.compress(msgs, current_tokens=999999, force=True)

        assert result == msgs
        assert c._last_summary_refused_failure is True
        assert c._last_compress_aborted is True
        # The refusal text must never be stored for iterative updates.
        assert c._previous_summary is None or "I can't produce this summary" not in (c._previous_summary or "")

    def test_refusal_falls_back_to_main_model_once(self):
        """With a distinct aux summary model, a refusal retries once on the main model
        (which may comply) and succeeds."""
        with patch("agent.context_compressor.get_model_context_length", return_value=100000):
            c = ContextCompressor(
                model="main-model",
                summary_model_override="small-aux-model",
                quiet_mode=True,
            )
        refused = _mock_response(_REFUSAL_TEXT, "stop")
        ok = _mock_response("full summary via main model", "stop")
        with patch(
            "agent.context_compressor.call_llm",
            side_effect=[refused, ok],
        ) as mock_call:
            result = c._generate_summary(_msgs(2))

        assert mock_call.call_count == 2
        assert result is not None
        assert "full summary via main model" in result
        assert c._last_summary_refused_failure is False

    def test_stop_finish_reason_still_succeeds(self):
        """Control: an ordinary, non-refusing summary is accepted unchanged."""
        with patch("agent.context_compressor.get_model_context_length", return_value=100000):
            c = ContextCompressor(model="test", quiet_mode=True)
        with patch(
            "agent.context_compressor.call_llm",
            return_value=_mock_response("complete summary", "stop"),
        ):
            result = c._generate_summary(_msgs(2))
        assert result is not None
        assert "complete summary" in result
        assert c._last_summary_refused_failure is False

    def test_successful_summary_clears_refused_flag(self):
        with patch("agent.context_compressor.get_model_context_length", return_value=100000):
            c = ContextCompressor(model="test", quiet_mode=True)
        c._last_summary_refused_failure = True
        c._summary_failure_cooldown_until = 0
        with patch(
            "agent.context_compressor.call_llm",
            return_value=_mock_response("fine", "stop"),
        ):
            result = c._generate_summary(_msgs(2))
        assert result is not None
        assert c._last_summary_refused_failure is False
