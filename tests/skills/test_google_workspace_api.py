"""Tests for Google Workspace gws bridge and CLI wrapper."""

import importlib.util
import json
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


BRIDGE_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills/productivity/google-workspace/scripts/gws_bridge.py"
)
API_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills/productivity/google-workspace/scripts/google_api.py"
)


@pytest.fixture
def bridge_module(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    spec = importlib.util.spec_from_file_location("gws_bridge_test", BRIDGE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api_module(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    spec = importlib.util.spec_from_file_location("gws_api_test", API_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    # Ensure the gws CLI code path is taken even when the binary isn't
    # installed (CI).  Without this, calendar_list() falls through to the
    # Python SDK path which imports ``googleapiclient`` — not in deps.
    module._gws_binary = lambda: "/usr/bin/gws"
    # Bypass authentication check — no real token file in CI.
    module._ensure_authenticated = lambda: None
    return module


def _write_token(path: Path, *, token="ya29.test", expiry=None, **extra):
    data = {
        "token": token,
        "refresh_token": "1//refresh",
        "client_id": "123.apps.googleusercontent.com",
        "client_secret": "secret",
        "token_uri": "https://oauth2.googleapis.com/token",
        **extra,
    }
    if expiry is not None:
        data["expiry"] = expiry
    path.write_text(json.dumps(data), encoding="utf-8")


def test_bridge_returns_valid_token(bridge_module, tmp_path):
    """Non-expired token is returned without refresh."""
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    token_path = bridge_module.get_token_path()
    _write_token(token_path, token="ya29.valid", expiry=future)

    result = bridge_module.get_valid_token()
    assert result == "ya29.valid"










def test_bridge_main_injects_token_env(bridge_module, tmp_path):
    """main() sets GOOGLE_WORKSPACE_CLI_TOKEN in subprocess env."""
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    token_path = bridge_module.get_token_path()
    _write_token(token_path, token="ya29.injected", expiry=future)

    captured = {}

    def capture_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env", {})
        return MagicMock(returncode=0)

    with patch.object(sys, "argv", ["gws_bridge.py", "gmail", "+triage"]):
        with patch.object(subprocess, "run", side_effect=capture_run):
            with pytest.raises(SystemExit):
                bridge_module.main()

    assert captured["env"]["GOOGLE_WORKSPACE_CLI_TOKEN"] == "ya29.injected"
    assert captured["cmd"] == ["gws", "gmail", "+triage"]


def test_api_calendar_list_uses_events_list(api_module):
    """calendar_list calls _run_gws with events list + params."""
    captured = {}

    def capture_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0, stdout="{}", stderr="")

    args = api_module.argparse.Namespace(
        start="", end="", max=25, calendar="primary", func=api_module.calendar_list,
    )

    with patch.object(api_module.subprocess, "run", side_effect=capture_run):
        api_module.calendar_list(args)

    cmd = captured["cmd"]
    # _gws_binary() returns "/usr/bin/gws", so cmd[0] is that binary
    assert cmd[0] == "/usr/bin/gws"
    assert "calendar" in cmd
    assert "events" in cmd
    assert "list" in cmd
    assert "--params" in cmd
    params = json.loads(cmd[cmd.index("--params") + 1])
    assert "timeMin" in params
    assert "timeMax" in params
    assert params["calendarId"] == "primary"












def test_api_get_credentials_refresh_persists_authorized_user_type(api_module, monkeypatch):
    token_path = api_module.TOKEN_PATH
    _write_token(token_path, token="ya29.old")

    class FakeCredentials:
        def __init__(self):
            self.expired = True
            self.refresh_token = "1//refresh"
            self.valid = True

        def refresh(self, request):
            self.expired = False

        def to_json(self):
            return json.dumps({
                "token": "ya29.refreshed",
                "refresh_token": "1//refresh",
                "client_id": "123.apps.googleusercontent.com",
                "client_secret": "secret",
                "token_uri": "https://oauth2.googleapis.com/token",
            })

    class FakeCredentialsModule:
        @staticmethod
        def from_authorized_user_file(filename, scopes):
            assert filename == str(token_path)
            assert scopes == api_module.SCOPES
            return FakeCredentials()

    google_module = types.ModuleType("google")
    oauth2_module = types.ModuleType("google.oauth2")
    credentials_module = types.ModuleType("google.oauth2.credentials")
    credentials_module.Credentials = FakeCredentialsModule
    transport_module = types.ModuleType("google.auth.transport")
    requests_module = types.ModuleType("google.auth.transport.requests")
    requests_module.Request = lambda: object()

    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.oauth2", oauth2_module)
    monkeypatch.setitem(sys.modules, "google.oauth2.credentials", credentials_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport", transport_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", requests_module)

    creds = api_module.get_credentials()

    saved = json.loads(token_path.read_text(encoding="utf-8"))
    assert isinstance(creds, FakeCredentials)
    assert saved["token"] == "ya29.refreshed"
    assert saved["type"] == "authorized_user"


def _tabbed_doc():
    """A Doc with two tabs (one nested), as the Docs API returns with includeTabsContent."""
    def body(text):
        return {"content": [
            {"endIndex": len(text) + 2,
             "paragraph": {"elements": [{"textRun": {"content": text + "\n"}}]}},
        ]}
    return {
        "title": "Tabbed",
        "documentId": "doc1",
        "tabs": [
            {
                "tabProperties": {"tabId": "t.0", "title": "First"},
                "documentTab": {"body": body("alpha")},
                "childTabs": [
                    {
                        "tabProperties": {"tabId": "t.0.a", "title": "Nested"},
                        "documentTab": {"body": body("beta")},
                    }
                ],
            },
            {
                "tabProperties": {"tabId": "t.1", "title": "Second"},
                "documentTab": {"body": body("gamma")},
            },
        ],
    }


def test_docs_get_returns_every_tab_of_a_tabbed_doc(api_module, monkeypatch, capsys):
    """A multi-tab Doc must not lose tab content: reads traverse the tabs tree
    (preorder, nested tabs included) instead of only the legacy top-level body."""
    monkeypatch.setattr(
        api_module, "_run_gws",
        lambda parts, params=None, body=None: _tabbed_doc(),
    )
    args = types.SimpleNamespace(doc_id="doc1", tab=None)
    api_module.docs_get(args)
    result = json.loads(capsys.readouterr().out)
    tabs = {t["tabId"]: t for t in result["tabs"]}
    assert set(tabs) == {"t.0", "t.0.a", "t.1"}
    assert tabs["t.0.a"]["body"] == "beta\n"
    assert tabs["t.0.a"]["level"] == 1
    # Multi-tab docs have no single merged "body" — index spaces are independent.
    assert "body" not in result


def test_docs_append_carries_tab_id_and_refuses_ambiguous_writes(api_module, monkeypatch, capsys):
    """Each tab has its own index space, so a write must target exactly one tab:
    the insert location carries the tabId, and an un-targeted write against a
    multi-tab doc errors instead of silently landing in the first tab."""
    monkeypatch.setattr(
        api_module, "_run_gws",
        lambda parts, params=None, body=None: _tabbed_doc(),
    )
    sent = {}
    monkeypatch.setattr(
        api_module, "_docs_insert_text",
        lambda doc_id, text, index, tab_id=None: sent.update(
            {"doc_id": doc_id, "index": index, "tab_id": tab_id}
        ),
    )

    api_module.docs_append(types.SimpleNamespace(doc_id="doc1", text="more", tab="t.1"))
    assert sent["tab_id"] == "t.1"
    assert sent["index"] == len("gamma") + 1  # endIndex - 1 within THAT tab's space
    capsys.readouterr()

    with pytest.raises(SystemExit):
        api_module.docs_append(types.SimpleNamespace(doc_id="doc1", text="more", tab=None))
    err = json.loads(capsys.readouterr().err)
    assert "tabs" in err and len(err["tabs"]) == 3


def test_next_birthday_occurrence_later_this_year(api_module):
    from datetime import date

    today = date(2026, 1, 1)
    assert api_module._next_birthday_occurrence(6, 15, today) == date(2026, 6, 15)


def test_next_birthday_occurrence_already_passed_wraps_to_next_year(api_module):
    from datetime import date

    today = date(2026, 9, 17)
    assert api_module._next_birthday_occurrence(1, 1, today) == date(2027, 1, 1)


def test_next_birthday_occurrence_today_is_zero_days_out(api_module):
    from datetime import date

    today = date(2026, 9, 17)
    assert api_module._next_birthday_occurrence(9, 17, today) == today


def test_next_birthday_occurrence_leap_day_falls_back_in_non_leap_year(api_module):
    """Feb 29 has no exact anniversary in a non-leap year; land on Feb 28."""
    from datetime import date

    today = date(2027, 1, 1)  # 2027 is not a leap year
    assert api_module._next_birthday_occurrence(2, 29, today) == date(2027, 2, 28)


def test_next_birthday_occurrence_leap_day_lands_exactly_in_leap_year(api_module):
    from datetime import date

    today = date(2028, 1, 1)  # 2028 is a leap year
    assert api_module._next_birthday_occurrence(2, 29, today) == date(2028, 2, 29)


def test_select_birthday_date_skips_entries_without_month_or_day(api_module):
    person = {"birthdays": [{"date": {"year": 1990}}, {"date": {"month": 5, "day": 4}}]}
    assert api_module._select_birthday_date(person) == {"month": 5, "day": 4}
    assert api_module._select_birthday_date({"birthdays": [{"date": {"year": 1990}}]}) is None
    assert api_module._select_birthday_date({}) is None


def test_format_birthday_display_with_and_without_year(api_module):
    assert api_module._format_birthday_display(1985, 9, 21) == "21.09.1985"
    assert api_module._format_birthday_display(None, 10, 4) == "04.10."


def test_contacts_birthdays_paginates_filters_sorts_and_limits(api_module, monkeypatch, capsys):
    """Wires pagination, ignores unusable entries, and sorts by daysUntil."""
    from datetime import date, timedelta

    today = date.today()
    near = today + timedelta(days=5)
    far = today + timedelta(days=200)

    page1 = {
        "connections": [
            {"names": [{"displayName": "No Birthday"}]},
            {
                "names": [{"displayName": "Year Only"}],
                "birthdays": [{"date": {"year": 1990}}],
            },
            {
                "names": [{"displayName": "Far Person"}],
                "birthdays": [{"date": {"month": far.month, "day": far.day}}],
            },
        ],
        "nextPageToken": "page2-token",
    }
    page2 = {
        "connections": [
            {
                "names": [{"displayName": "Near Person"}],
                "birthdays": [{"date": {"year": 1985, "month": near.month, "day": near.day}}],
            },
        ],
    }

    calls = []

    def fake_run_gws(parts, params=None, body=None):
        calls.append(params)
        return page2 if params.get("pageToken") else page1

    monkeypatch.setattr(api_module, "_run_gws", fake_run_gws)

    api_module.contacts_birthdays(types.SimpleNamespace(days=None, max=100))
    result = json.loads(capsys.readouterr().out)

    # Both pages were fetched.
    assert len(calls) == 2
    assert calls[1]["pageToken"] == "page2-token"

    # "No Birthday" and "Year Only" are excluded; the rest are sorted soonest-first.
    assert [r["name"] for r in result] == ["Near Person", "Far Person"]

    expected_near_next = api_module._next_birthday_occurrence(near.month, near.day, today)
    near_record = result[0]
    assert near_record["nextDate"] == expected_near_next.isoformat()
    assert near_record["daysUntil"] == (expected_near_next - today).days
    assert near_record["turningAge"] == expected_near_next.year - 1985
    assert near_record["birthday"] == f"{near.day:02d}.{near.month:02d}.1985"

    far_record = result[1]
    assert "turningAge" not in far_record
    assert far_record["birthday"] == f"{far.day:02d}.{far.month:02d}."

    # --days filters out contacts whose next occurrence falls outside the horizon.
    api_module.contacts_birthdays(types.SimpleNamespace(days=30, max=100))
    filtered = json.loads(capsys.readouterr().out)
    assert [r["name"] for r in filtered] == ["Near Person"]

    # --max caps the (already sorted) output.
    api_module.contacts_birthdays(types.SimpleNamespace(days=None, max=1))
    capped = json.loads(capsys.readouterr().out)
    assert [r["name"] for r in capped] == ["Near Person"]
