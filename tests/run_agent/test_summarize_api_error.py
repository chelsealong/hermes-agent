"""Regression: empty-body HTTP 4xx errors must still surface a real provider message.

Reported on Windows (#36109): an LLM API call returned HTTP 400 with an *empty*
parsed SDK ``body`` ({}), so ``_summarize_api_error`` fell through to the bare
``str(error)`` path and the user saw only "HTTP 400" with no provider detail.
The SDK leaves ``body`` empty in this case, but the underlying httpx
``response`` still carries the real payload in ``.text``. These tests lock the
contract: when ``body`` is empty, fall back to ``response.text`` (parsing a JSON
``error.message`` / ``message`` when present) so logs and CLI show the real
provider error. This is a diagnostic improvement and is platform-agnostic.
"""

from types import SimpleNamespace
from typing import Any

import httpx

from run_agent import AIAgent


def _make_empty_body_error(response_text: str, status_code: int = 400) -> Exception:
    """Mimic an OpenAI-SDK error whose parsed body is empty but whose httpx
    response still holds the payload text."""
    err = Exception("")  # str(error) is empty/uninformative on this path
    err.status_code = status_code
    err.body = {}  # empty dict — the #36109 trigger
    err.response = SimpleNamespace(text=response_text)
    return err


def test_empty_body_falls_back_to_response_json_error_message():
    """A JSON payload with error.message is surfaced (not a bare HTTP 400)."""
    err = _make_empty_body_error(
        '{"error": {"message": "model `foo` does not exist", "type": "invalid_request_error"}}'
    )
    summary = AIAgent._summarize_api_error(err)
    assert "HTTP 400" in summary
    assert "model `foo` does not exist" in summary






def test_bare_404_names_the_model_when_context_given():
    """A 404 whose only content is a plain-text "page not found" page
    (#78796) must name the requested model instead of reading like a
    generic outage. Real SDK ``NotFoundError``s carry this text via
    ``error.response.text`` (see ``_make_empty_body_error``), not as a bare
    ``str(error)`` — that shape must hit this same annotation, not just the
    bottom-of-function fallback for errors with no ``.response`` at all."""
    err = _make_empty_body_error("404 page not found", status_code=404)

    summary = AIAgent._summarize_api_error(
        err, model="nemotron-3-ultra-550b-a55b", provider="nvidia"
    )
    assert "nemotron-3-ultra-550b-a55b" in summary
    assert "nvidia" in summary
    assert "404 page not found" in summary


def test_bare_404_without_model_context_is_unchanged():
    """No model/provider passed → same bare summary as before (back-compat
    for callers, e.g. cli.py, that don't supply this context)."""
    err = _make_empty_body_error("404 page not found", status_code=404)

    summary = AIAgent._summarize_api_error(err)
    assert summary == "HTTP 404: 404 page not found"


def test_bare_404_no_response_attribute_still_names_the_model():
    """Belt-and-suspenders: an error with no ``.response``/``.body`` at all
    (the bottom-of-function fallback) must also get the annotation."""
    err = Exception("404 page not found")
    err.status_code = 404

    summary = AIAgent._summarize_api_error(
        err, model="nemotron-3-ultra-550b-a55b", provider="nvidia"
    )
    assert "nemotron-3-ultra-550b-a55b" in summary
    assert "nvidia" in summary


def test_404_does_not_double_name_a_model_already_in_the_message():
    """When the provider's own message already names the model, don't
    prepend a redundant annotation on top of it."""
    err = _make_empty_body_error(
        '{"error": {"message": "model `nemotron-3-ultra-550b-a55b` does not exist"}}',
        status_code=404,
    )

    summary = AIAgent._summarize_api_error(
        err, model="nemotron-3-ultra-550b-a55b", provider="nvidia"
    )
    assert summary.count("nemotron-3-ultra-550b-a55b") == 1
    assert "HTTP 404 (model=" not in summary


def test_unread_streaming_response_does_not_crash_and_falls_back_to_exception_message():
    """Unread streaming responses must not replace the real provider error."""

    class _StreamingError(Exception):
        def __init__(self):
            super().__init__("Gemini HTTP 429: quota exceeded")
            self.status_code = 429
            self.response: Any = None

    err = _StreamingError()

    class _UnreadStreamingResponse:
        @property
        def text(self):
            raise httpx.ResponseNotRead()

    err.response = _UnreadStreamingResponse()
    summary = AIAgent._summarize_api_error(err)
    assert "HTTP 429" in summary
    assert "Gemini HTTP 429: quota exceeded" in summary

