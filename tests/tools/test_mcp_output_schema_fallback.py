"""Tests for MCP output-schema tolerance (#101330).

When an MCP server returns ``structuredContent`` that violates its own
advertised ``outputSchema``, the MCP SDK's ``ClientSession.call_tool`` raises
``RuntimeError`` and the whole ``CallToolResult`` is discarded — including the
``result.content`` text block, which carries the same payload. The old Hermes
behavior then (a) lost that usable content and (b) counted the failure toward
the per-server circuit breaker, so three such calls took the connector's
healthy tools offline too.

These tests lock in the fix: a schema-invalid response degrades to
``result.content`` and does NOT advance the breaker.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tools import mcp_tool


# A schema the fake server violates: ``answer`` is required and must be a
# string, but the server returns it as null.
_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}


class _FakeContentBlock:
    def __init__(self, text: str, block_type: str = "text"):
        self.text = text
        self.type = block_type


class _FakeCallToolResult:
    def __init__(self, content, is_error=False, structuredContent=None):
        self.content = content
        self.isError = is_error
        self.structuredContent = structuredContent


def _fake_run_on_mcp_loop(coro_or_factory, timeout=30):
    coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
    loop = asyncio.new_event_loop()
    try:
        async def _install_lock_and_run():
            for srv in list(mcp_tool._servers.values()):
                if getattr(srv, "_rpc_lock", None) is None:
                    srv._rpc_lock = asyncio.Lock()
            return await coro
        return loop.run_until_complete(_install_lock_and_run())
    finally:
        loop.close()


def _make_schema_validating_call_tool(session, result):
    """Return an async ``call_tool`` that mimics the real SDK.

    It validates ``result.structuredContent`` against the tool's *cached*
    output schema (``session._tool_output_schemas``) and raises the same
    ``RuntimeError`` the SDK raises when validation fails. When Hermes has
    cleared the cached schema (the fix), validation is skipped and the raw
    result — content intact — is returned.
    """
    async def _call_tool(name, arguments=None, **kwargs):
        cached = session._tool_output_schemas.get(name)
        if cached is not None and not result.isError:
            from jsonschema import ValidationError, validate
            try:
                validate(result.structuredContent, cached)
            except ValidationError as exc:
                raise RuntimeError(
                    f"Invalid structured content returned by tool {name}: {exc}"
                )
        return result
    return _call_tool


@pytest.fixture
def _patched_server():
    fake_session = MagicMock()
    fake_session._tool_output_schemas = {"my-tool": dict(_OUTPUT_SCHEMA)}
    fake_server = SimpleNamespace(session=fake_session, _rpc_lock=None)
    mcp_tool._server_error_counts.pop("test-server", None)
    # ``_schema_warning_seen`` is introduced by the fix; guard so the test can
    # still be *collected* against an unfixed build and fail on the behavioral
    # assertions (content discarded / breaker tripped) rather than an import.
    getattr(mcp_tool, "_schema_warning_seen", set()).discard(
        ("test-server", "my-tool")
    )
    with patch.dict(mcp_tool._servers, {"test-server": fake_server}), \
         patch("tools.mcp_tool._run_on_mcp_loop", side_effect=_fake_run_on_mcp_loop):
        yield fake_session
    mcp_tool._server_error_counts.pop("test-server", None)
    getattr(mcp_tool, "_schema_warning_seen", set()).discard(
        ("test-server", "my-tool")
    )


def test_schema_violation_preserves_content_and_spares_breaker(_patched_server):
    """A schema-invalid result must degrade to content, not trip the breaker."""
    session = _patched_server
    result = _FakeCallToolResult(
        content=[_FakeContentBlock("the answer is 42")],
        structuredContent={"answer": None},  # violates required string
    )
    session.call_tool = _make_schema_validating_call_tool(session, result)

    handler = mcp_tool._make_tool_handler("test-server", "my-tool", 30.0)
    raw = handler({})
    data = json.loads(raw)

    # Content preserved (would be an {"error": ...} discard without the fix).
    assert "error" not in data, data
    assert data["result"] == "the answer is 42"
    assert data["structuredContent"] == {"answer": None}

    # The breaker was NOT advanced — the server is reachable, only its
    # schema is wrong. Without the fix this would be >= 1.
    assert mcp_tool._server_error_counts.get("test-server", 0) == 0

    # The tool's real advertised schema is restored for future calls.
    assert session._tool_output_schemas["my-tool"] == _OUTPUT_SCHEMA


def test_schema_violation_warns_once(_patched_server):
    """Repeated violations from the same (server, tool) warn only once."""
    session = _patched_server
    result = _FakeCallToolResult(
        content=[_FakeContentBlock("payload")],
        structuredContent={"answer": None},
    )
    session.call_tool = _make_schema_validating_call_tool(session, result)
    handler = mcp_tool._make_tool_handler("test-server", "my-tool", 30.0)

    with patch.object(mcp_tool.logger, "warning") as warn:
        handler({})
        handler({})
        handler({})

    assert warn.call_count == 1, warn.call_args_list


def test_schema_valid_result_unaffected(_patched_server):
    """A schema-*valid* structured result flows through untouched."""
    session = _patched_server
    result = _FakeCallToolResult(
        content=[_FakeContentBlock("ok")],
        structuredContent={"answer": "yes"},  # valid
    )
    session.call_tool = _make_schema_validating_call_tool(session, result)

    handler = mcp_tool._make_tool_handler("test-server", "my-tool", 30.0)
    with patch.object(mcp_tool.logger, "warning") as warn:
        data = json.loads(handler({}))

    assert data["result"] == "ok"
    assert data["structuredContent"] == {"answer": "yes"}
    assert warn.call_count == 0
    assert mcp_tool._server_error_counts.get("test-server", 0) == 0
