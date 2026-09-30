"""Desktop agents resumed from a messaging-origin session pass its chat key as ``gateway_session_key``.

Regression for #128675: ``_make_agent`` never forwarded it, so memory providers scoped the desktop half of a
Telegram thread differently from the gateway half.
"""

from __future__ import annotations

import types

from tui_gateway import server


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def get_session(self, session_id):
        return self.rows.get(session_id)


def _built(monkeypatch, db, session_id):
    captured = {}
    monkeypatch.setattr("run_agent.AIAgent", lambda **kw: captured.update(kw) or types.SimpleNamespace())
    monkeypatch.setattr(server, "_resolve_agent_model_runtime", lambda *_a: ("test-model", {}))
    monkeypatch.setattr(server, "_load_enabled_toolsets", lambda *_a, **_kw: None)
    monkeypatch.setattr(server, "_agent_cbs", lambda sid: {})
    server._make_agent("sid", session_id, session_id=session_id, session_db=db)
    return captured


def test_messaging_origin_session_keeps_its_gateway_key(monkeypatch):
    db = _DB({"s1": {"session_key": "agent:main:telegram:group:-100:7"}})
    assert _built(monkeypatch, db, "s1")["gateway_session_key"] == "agent:main:telegram:group:-100:7"


def test_desktop_born_session_has_no_gateway_key(monkeypatch):
    db = _DB({"s2": {"session_key": None}})
    assert _built(monkeypatch, db, "s2")["gateway_session_key"] is None
    assert _built(monkeypatch, db, "missing")["gateway_session_key"] is None
