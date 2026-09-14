"""Inbound dispatch + dedup tests for PhotonAdapter.

These bypass the loopback HTTP stream — they call ``_dispatch_inbound`` /
``_on_inbound_line`` / ``_dedup`` directly, exercising the
sidecar-event parsing without spawning the Node sidecar or binding ports.
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.event import MessageEvent, MessageType
from plugins.platforms.photon.adapter import PhotonAdapter


def _make_adapter(monkeypatch: pytest.MonkeyPatch) -> PhotonAdapter:
    monkeypatch.setenv("PHOTON_PROJECT_ID", "test-project-id")
    monkeypatch.setenv("PHOTON_PROJECT_SECRET", "test-project-secret")
    cfg = PlatformConfig(enabled=True, token="", extra={})
    return PhotonAdapter(cfg)


def _capture(adapter: PhotonAdapter, monkeypatch: pytest.MonkeyPatch) -> List[MessageEvent]:
    captured: List[MessageEvent] = []

    async def fake_handle(event: MessageEvent) -> None:
        captured.append(event)

    monkeypatch.setattr(adapter, "handle_message", fake_handle)
    return captured


def _dm_event(text: str, msg_id: str = "spc-msg-abc") -> Dict[str, Any]:
    return {
        "messageId": msg_id,
        "platform": "iMessage",
        "space": {"id": "+15551234567", "type": "dm", "phone": "+15551234567"},
        "sender": {"id": "+15551234567"},
        "content": {"type": "text", "text": text},
        "timestamp": "2026-05-14T19:06:32.000Z",
    }


@pytest.mark.asyncio
async def test_dispatch_text_dm(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _make_adapter(monkeypatch)
    captured = _capture(adapter, monkeypatch)

    await adapter._dispatch_inbound(_dm_event("hello world"))

    assert len(captured) == 1
    event = captured[0]
    assert event.text == "hello world"
    assert event.message_type == MessageType.TEXT
    assert event.message_id == "spc-msg-abc"
    src = event.source
    assert src is not None
    assert src.platform == Platform("photon")
    assert src.chat_id == "+15551234567"
    assert src.chat_type == "dm"
    assert src.user_id == "+15551234567"


@pytest.mark.asyncio
async def test_dispatch_read_receipt_does_not_wake_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter(monkeypatch)
    captured = _capture(adapter, monkeypatch)
    receipt = _dm_event("", msg_id="spc-read-1")
    receipt["content"] = {
        "type": "read",
        "targetMessageId": "bot-msg-1",
        "targetDirection": "outbound",
    }

    await adapter._dispatch_inbound(receipt)

    assert captured == []


@pytest.mark.asyncio
async def test_dispatch_read_receipt_alias_does_not_wake_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Some spectrum-ts streams label receipts ``read_receipt`` — same drop."""
    adapter = _make_adapter(monkeypatch)
    captured = _capture(adapter, monkeypatch)
    receipt = _dm_event("", msg_id="spc-read-2")
    receipt["content"] = {
        "type": "read_receipt",
        "targetMessageId": "bot-msg-2",
        "targetDirection": "outbound",
    }

    await adapter._dispatch_inbound(receipt)

    assert captured == []


# A real 1x1 transparent PNG (passes base.py's _looks_like_image magic check).
_PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhf"
    "DwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _attachment_event(
    content: Dict[str, Any], msg_id: str = "spc-msg-att"
) -> Dict[str, Any]:
    return {
        "messageId": msg_id,
        "space": {"id": "+15551234567", "type": "dm", "phone": "+15551234567"},
        "sender": {"id": "+15551234567"},
        "content": {"type": "attachment", **content},
        "timestamp": "2026-05-14T19:06:32.000Z",
    }


def _voice_event(
    content: Dict[str, Any], msg_id: str = "spc-msg-voice"
) -> Dict[str, Any]:
    return {
        "messageId": msg_id,
        "space": {"id": "+15551234567", "type": "dm", "phone": "+15551234567"},
        "sender": {"id": "+15551234567"},
        "content": {"type": "voice", **content},
        "timestamp": "2026-05-14T19:06:32.000Z",
    }


@pytest.mark.asyncio
async def test_on_inbound_line_dispatches_and_dedups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter(monkeypatch)
    captured = _capture(adapter, monkeypatch)

    line = json.dumps(_dm_event("ping", msg_id="dup-1"))
    await adapter._on_inbound_line(line)
    await adapter._on_inbound_line(line)  # same messageId -> deduped

    assert len(captured) == 1
    assert captured[0].text == "ping"


def test_is_duplicate_window(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _make_adapter(monkeypatch)
    assert adapter._dedup.is_duplicate("id-1") is False
    assert adapter._dedup.is_duplicate("id-1") is True
    assert adapter._dedup.is_duplicate("id-2") is False
    assert adapter._dedup.is_duplicate("id-1") is True  # still dup


class _FakeTextStreamResponse:
    """Minimal stand-in for httpx.Response exposing only ``aiter_text()``."""

    def __init__(self, chunks: List[str]) -> None:
        self._chunks = chunks

    async def aiter_text(self):
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
async def test_iter_ndjson_lines_keeps_unicode_line_separator_whole() -> None:
    """A multi-line iMessage embeds a raw U+2028 inside the JSON string (valid
    JSON, but not a line terminator we should split on). Splitting on U+2028 -
    the way httpx's aiter_lines()/str.splitlines() does - would fragment the
    NDJSON record into two halves that both fail json.loads."""
    text = "line one" + chr(0x2028) + "line two"  # iOS Enter -> raw U+2028, unescaped by JSON.stringify
    event = {"messageId": "m1", "content": {"type": "text", "text": text}}
    record = json.dumps(event, ensure_ascii=False)  # match JS JSON.stringify: raw U+2028, not \u2028
    resp = _FakeTextStreamResponse([record + "\n"])

    lines = [line async for line in PhotonAdapter._iter_ndjson_lines(resp)]

    assert lines == [record]
    assert json.loads(lines[0]) == event


@pytest.mark.asyncio
async def test_iter_ndjson_lines_splits_only_on_newline_across_chunks() -> None:
    resp = _FakeTextStreamResponse(['{"a":', '1}\n{"b":2}\n'])

    lines = [line async for line in PhotonAdapter._iter_ndjson_lines(resp)]

    assert lines == ['{"a":1}', '{"b":2}']


class _FakeStreamCtx:
    def __init__(self, resp: Any) -> None:
        self._resp = resp

    async def __aenter__(self) -> Any:
        return self._resp

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakeInboundResponse:
    """Mimics httpx.Response for the /inbound stream, implementing both read
    paths: ``aiter_lines()`` reproduces httpx's real ``str.splitlines()``-based
    splitting (which also breaks on U+2028/U+2029/U+0085), and ``aiter_text()``
    yields raw chunks. The same fake drives both the pre-fix and post-fix code."""

    status_code = 200

    def __init__(self, chunks: List[str]) -> None:
        self._chunks = chunks

    async def aiter_text(self):
        for chunk in self._chunks:
            yield chunk

    async def aiter_lines(self):
        for line in "".join(self._chunks).splitlines():
            yield line


class _FakeInboundClient:
    def __init__(self, resp: Any) -> None:
        self._resp = resp

    def stream(self, method: str, url: str, headers: Any = None, timeout: Any = None) -> _FakeStreamCtx:
        return _FakeStreamCtx(self._resp)


@pytest.mark.asyncio
async def test_inbound_loop_delivers_message_with_embedded_line_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end regression for the dropped-iMessage bug: the sidecar's NDJSON
    line for a multi-line iMessage contains a raw U+2028 (``JSON.stringify``
    does not escape it). Reading the stream with httpx's ``aiter_lines()``
    also splits on U+2028 and fragments one record into two invalid-JSON
    halves that ``_on_inbound_line`` silently drops - the message never
    reaches ``handle_message``."""
    adapter = _make_adapter(monkeypatch)
    captured = _capture(adapter, monkeypatch)

    text = "line one" + chr(0x2028) + "line two"
    event = _dm_event(text, msg_id="spc-msg-u2028")
    record = json.dumps(event, ensure_ascii=False)  # match JS JSON.stringify: raw U+2028, not \u2028
    adapter._http_client = _FakeInboundClient(_FakeInboundResponse([record + "\n"]))

    orig_on_line = adapter._on_inbound_line

    async def _on_line_then_stop(line: str) -> None:
        await orig_on_line(line)
        adapter._inbound_running = False

    monkeypatch.setattr(adapter, "_on_inbound_line", _on_line_then_stop)

    adapter._inbound_running = True
    await asyncio.wait_for(adapter._inbound_loop(), timeout=2.0)

    assert len(captured) == 1
    assert captured[0].text == text


def test_check_requirements_without_node(monkeypatch: pytest.MonkeyPatch) -> None:
    # If no node binary on PATH the adapter should refuse to start.
    from plugins.platforms.photon import adapter as adapter_mod

    monkeypatch.setattr(adapter_mod.shutil, "which", lambda _name: None)
    assert adapter_mod.check_requirements() is False


# ---------------------------------------------------------------------------
# CAF attachment promotion + U+FFFC placeholder tests
# ---------------------------------------------------------------------------

_CAF_BYTES = b"caff" + b"\x00" * 60  # Minimal CAF header magic


def _caf_attachment_event(
    content: Dict[str, Any], msg_id: str = "spc-msg-caf"
) -> Dict[str, Any]:
    return {
        "messageId": msg_id,
        "space": {"id": "+155****4567", "type": "dm", "phone": "+155****4567"},
        "sender": {"id": "+155****4567"},
        "content": {"type": "attachment", **content},
        "timestamp": "2026-05-14T19:06:32.000Z",
    }


@pytest.mark.asyncio
async def test_caf_attachment_named_promoted_to_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A named .caf attachment is promoted to VOICE for STT routing."""
    adapter = _make_adapter(monkeypatch)
    captured = _capture(adapter, monkeypatch)

    raw = _CAF_BYTES
    event = _caf_attachment_event(
        {
            "name": "voice_note.caf",
            "mimeType": "audio/x-caf",
            "size": len(raw),
            "data": base64.b64encode(raw).decode("ascii"),
            "encoding": "base64",
        }
    )
    await adapter._dispatch_inbound(event)

    assert len(captured) == 1
    ev = captured[0]
    assert ev.message_type == MessageType.VOICE
    assert ev.media_types == ["audio/x-caf"]
    assert len(ev.media_urls) == 1
    cached = Path(ev.media_urls[0])
    try:
        assert cached.is_file()
        assert cached.read_bytes() == raw
        assert ev.text == "(voice)"
    finally:
        cached.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_fffc_placeholder_no_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A U+FFFC placeholder text does not trigger a message dispatch."""
    adapter = _make_adapter(monkeypatch)
    captured = _capture(adapter, monkeypatch)

    event = _dm_event("\ufffc", msg_id="spc-msg-fffc")
    chat_key = event["space"]["id"]
    await adapter._dispatch_inbound(event)

    assert len(captured) == 0
    assert chat_key in adapter._pending_fffc


@pytest.mark.asyncio
async def test_disconnect_cancels_pending_fffc_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """disconnect() cancels any pending U+FFFC placeholder tasks."""
    adapter = _make_adapter(monkeypatch)
    _capture(adapter, monkeypatch)

    await adapter._dispatch_inbound(_dm_event("\ufffc", msg_id="spc-msg-fffc"))
    assert len(adapter._pending_fffc) == 1

    async def _noop_stop_sidecar():
        pass

    monkeypatch.setattr(adapter, "_stop_sidecar", _noop_stop_sidecar)
    monkeypatch.setattr(adapter, "_inbound_running", False)
    monkeypatch.setattr(adapter, "_inbound_task", None)
    monkeypatch.setattr(adapter, "_sidecar_health_task", None)
    monkeypatch.setattr(adapter, "_http_client", None)

    await adapter.disconnect()

    assert len(adapter._pending_fffc) == 0
