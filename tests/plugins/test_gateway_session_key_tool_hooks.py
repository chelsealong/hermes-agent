"""``gateway_session_key`` must reach ``pre_tool_call``/``post_tool_call`` plugin hooks
(#118717), not just the transcript ``session_id``.
"""

import model_tools

_UNSET = object()


def _run_handle_function_call(
    monkeypatch,
    *,
    tool_name="dummy_tool",
    tool_args=None,
    dispatch_result='{"output": "original"}',
    invoke_hook=_UNSET,
    gateway_session_key="agent:default:discord:dm:fixture",
):
    from tools.registry import registry

    monkeypatch.setattr(registry, "dispatch", lambda name, args, **kw: dispatch_result)
    monkeypatch.setattr(model_tools, "_READ_SEARCH_TOOLS", frozenset())

    if invoke_hook is not _UNSET:
        monkeypatch.setattr("hermes_cli.plugins.invoke_hook", invoke_hook)
        monkeypatch.setattr("hermes_cli.plugins.has_hook", lambda name: True)

    return model_tools.handle_function_call(
        tool_name,
        tool_args or {},
        task_id="t1",
        session_id="s1",
        tool_call_id="tc1",
        gateway_session_key=gateway_session_key,
    )


def test_pre_and_post_tool_call_hooks_receive_gateway_session_key(monkeypatch):
    captured = {}

    def _hook(hook_name, **kwargs):
        if hook_name in ("pre_tool_call", "post_tool_call"):
            captured[hook_name] = kwargs.get("gateway_session_key")
        return []

    out = _run_handle_function_call(monkeypatch, invoke_hook=_hook)
    assert out == '{"output": "original"}'
    assert captured["pre_tool_call"] == "agent:default:discord:dm:fixture"
    assert captured["post_tool_call"] == "agent:default:discord:dm:fixture"


def test_gateway_session_key_defaults_to_empty_string(monkeypatch):
    captured = {}

    def _hook(hook_name, **kwargs):
        if hook_name in ("pre_tool_call", "post_tool_call"):
            captured[hook_name] = kwargs.get("gateway_session_key")
        return []

    _run_handle_function_call(monkeypatch, invoke_hook=_hook, gateway_session_key=None)
    assert captured["pre_tool_call"] == ""
    assert captured["post_tool_call"] == ""
