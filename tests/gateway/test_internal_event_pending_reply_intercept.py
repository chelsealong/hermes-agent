"""Regression for issue #125781: an internal (synthetic) event must never answer or
cancel a pending human clarification through ``_hm_pending_reply_intercepts``."""

import pytest

from gateway.config import Platform
from gateway.platforms.event import MessageEvent, MessageType
from gateway.session import SessionSource


def _clear_clarify_state():
    from tools import clarify_gateway as cm

    with cm._lock:
        cm._entries.clear()
        cm._session_index.clear()
        cm._notify_cbs.clear()


def _runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    # No live adapters are wired up; the clarify paths only need a delivery adapter to
    # (optionally) retire a native card, which is not under test here.
    runner._delivery_adapter_for = lambda source: None
    return runner


def _event(text: str, internal: bool) -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="private", user_id="u1"),
        message_id="m1",
        internal=internal,
    )


@pytest.mark.asyncio
async def test_internal_event_does_not_cancel_pending_choice_clarify():
    """An internal completion event must not cancel a pending choice clarify."""
    _clear_clarify_state()
    from tools import clarify_gateway as cm

    session_key = "sess-choice"
    cm.register("clarify-1", session_key, "Continue?", ["Accept", "Cancel"])
    runner = _runner()
    event = _event("background task completed", internal=True)

    reply = await runner._hm_pending_reply_intercepts(event, event.source, session_key)

    assert reply is None
    pending = cm.get_pending_for_session(session_key, include_choice_prompts=True)
    assert pending is not None
    assert pending.response is None


@pytest.mark.asyncio
async def test_internal_event_does_not_answer_pending_open_ended_clarify():
    """An internal completion event must not answer a pending open-ended clarify."""
    _clear_clarify_state()
    from tools import clarify_gateway as cm

    session_key = "sess-open"
    cm.register("clarify-2", session_key, "What would you like to do next?", None)
    runner = _runner()
    event = _event("background task completed", internal=True)

    reply = await runner._hm_pending_reply_intercepts(event, event.source, session_key)

    assert reply is None
    pending = cm.get_pending_for_session(session_key, include_choice_prompts=True)
    assert pending is not None
    assert pending.response is None


@pytest.mark.asyncio
async def test_human_event_still_resolves_pending_open_ended_clarify():
    """Sanity check: a genuine (non-internal) reply must still resolve the clarify."""
    _clear_clarify_state()
    from tools import clarify_gateway as cm

    session_key = "sess-human"
    cm.register("clarify-3", session_key, "What would you like to do next?", None)
    runner = _runner()
    event = _event("go ahead", internal=False)

    reply = await runner._hm_pending_reply_intercepts(event, event.source, session_key)

    assert reply == ""
    pending = cm.get_pending_for_session(session_key, include_choice_prompts=True)
    assert pending is not None
    assert pending.response == "go ahead"
