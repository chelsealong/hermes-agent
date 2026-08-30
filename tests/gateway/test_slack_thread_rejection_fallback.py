"""Regression: a non-threadable thread root must not drop the response.

Issue: Slack rejects ``chat.postMessage`` with ``thread_ts`` set to a
non-threadable root (e.g. a Slack system event such as a channel rename)
using ``cannot_reply_to_message``. send() must retry as a top-level channel
message instead of surfacing the failure and losing the reply.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.slack.adapter import SlackAdapter


class SlackThreadRejection(Exception):
    def __init__(self, error="cannot_reply_to_message"):
        super().__init__(f"The request to the Slack API failed. ({error})")
        self.response = {"error": error}


def _make_adapter():
    config = PlatformConfig(enabled=True, token="xoxb-fake")
    a = SlackAdapter(config)
    a._app = MagicMock()
    a.stop_typing = AsyncMock()
    a._running = True
    return a


class TestThreadRejectionFallback:
    @pytest.mark.asyncio
    async def test_cannot_reply_to_message_retries_without_thread_ts(self):
        adapter = _make_adapter()
        client = AsyncMock()
        client.chat_postMessage = AsyncMock(
            side_effect=[SlackThreadRejection(), {"ts": "111.222"}]
        )
        adapter._get_client = MagicMock(return_value=client)

        result = await adapter.send(
            "C123", "hello", reply_to="999.111", metadata={"thread_id": "999.111"}
        )

        assert result.success is True
        assert client.chat_postMessage.await_count == 2
        first_kwargs = client.chat_postMessage.await_args_list[0].kwargs
        second_kwargs = client.chat_postMessage.await_args_list[1].kwargs
        assert first_kwargs["thread_ts"] == "999.111"
        assert "thread_ts" not in second_kwargs
        # Assistant status must still be cleared even though the message
        # ultimately posted at the top level (#24117 regression guard).
        adapter.stop_typing.assert_awaited()

    @pytest.mark.asyncio
    async def test_unrelated_error_still_raises_and_is_reported(self):
        adapter = _make_adapter()
        client = AsyncMock()
        client.chat_postMessage = AsyncMock(
            side_effect=SlackThreadRejection(error="channel_not_found")
        )
        adapter._get_client = MagicMock(return_value=client)

        result = await adapter.send(
            "C123", "hello", reply_to="999.111", metadata={"thread_id": "999.111"}
        )

        assert result.success is False
        assert client.chat_postMessage.await_count == 1
