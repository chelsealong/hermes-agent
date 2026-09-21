"""A degenerate near-empty completion (finish_reason=stop, body a few characters) has no
truncation or failure signal, so nothing suppresses it — it is delivered as the job's result and
recorded as ``last_status: ok`` (#118367). ``min_response_chars`` gives an opt-in gate for it;
default (0, off) must not change existing behavior."""

from cron import scheduler
from cron.scheduler import _final_response_from_result


class _AIAgent:
    @staticmethod
    def _format_turn_completion_explanation(*args, **kwargs):
        return ""


def _stop_result(final_response: str) -> dict:
    # Mirrors the report's debug lines: a healthy, non-truncated turn with a trivially short body.
    return {
        "final_response": final_response, "failed": False, "completed": True, "model": "llama3",
        "turn_exit_reason": "text_response(finish_reason=stop)", "messages": [], "api_calls": 1,
    }


def test_short_stop_completion_delivered_by_default():
    result = _stop_result("🔍 **")
    assert _final_response_from_result(result, "job1", "Daily report", _AIAgent) == "🔍 **"


def test_short_stop_completion_suppressed_when_min_response_chars_set(monkeypatch):
    monkeypatch.setattr(scheduler, "_resolve_min_response_chars", lambda: 40)
    result = _stop_result("🔍 **")
    assert _final_response_from_result(result, "job1", "Daily report", _AIAgent) == ""


def test_response_at_or_above_threshold_still_delivered(monkeypatch):
    monkeypatch.setattr(scheduler, "_resolve_min_response_chars", lambda: 5)
    result = _stop_result("Done.")
    assert _final_response_from_result(result, "job1", "Daily report", _AIAgent) == "Done."


def test_silent_marker_not_swallowed_by_the_threshold(monkeypatch):
    monkeypatch.setattr(scheduler, "_resolve_min_response_chars", lambda: 40)
    result = _stop_result("[SILENT]")
    assert _final_response_from_result(result, "job1", "Daily report", _AIAgent) == "[SILENT]"


def test_cron_failure_marker_not_swallowed_by_the_threshold(monkeypatch):
    monkeypatch.setattr(scheduler, "_resolve_min_response_chars", lambda: 40)
    result = _stop_result("[CRON_FAILURE]\nboom")
    assert _final_response_from_result(result, "job1", "Daily report", _AIAgent) == "[CRON_FAILURE]\nboom"


def test_resolve_min_response_chars_env_override(monkeypatch):
    monkeypatch.setenv("HERMES_CRON_MIN_RESPONSE_CHARS", "12")
    assert scheduler._resolve_min_response_chars() == 12


def test_resolve_min_response_chars_defaults_to_off(monkeypatch):
    monkeypatch.delenv("HERMES_CRON_MIN_RESPONSE_CHARS", raising=False)
    monkeypatch.setattr(scheduler, "load_config", lambda: {})
    assert scheduler._resolve_min_response_chars() == 0
