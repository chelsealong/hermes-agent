"""``hermes gateway setup`` must ask "start the installed-but-stopped service?" at most once
per run (#121130). ``_wizard_service_status_block`` already asks it before the platform loop;
``_wizard_post_setup`` re-checked the same installed/not-running state afterwards and asked
again when the user had declined (or the start failed), duplicating the same question."""

import hermes_cli.gateway as gateway_mod


def _patch_common(monkeypatch, *, decline_start: bool):
    monkeypatch.setattr(gateway_mod, "is_managed", lambda: False)
    monkeypatch.setattr(gateway_mod, "supports_systemd_services", lambda: True)
    monkeypatch.setattr(gateway_mod, "has_conflicting_systemd_units", lambda: False)
    monkeypatch.setattr(gateway_mod, "has_legacy_hermes_units", lambda: False)
    monkeypatch.setattr(gateway_mod, "_system_scope_wizard_would_need_root", lambda **kw: False)
    monkeypatch.setattr(gateway_mod, "_served_profile_needs_no_service", lambda: False)
    # Installed but stopped, and stays stopped (declined / failed start) for the whole run.
    monkeypatch.setattr(gateway_mod, "_is_service_installed", lambda: True)
    monkeypatch.setattr(gateway_mod, "_is_service_running", lambda: False)
    monkeypatch.setattr(gateway_mod, "_setup_service_action", lambda *a, **kw: None)

    monkeypatch.setattr(gateway_mod, "_all_platforms", lambda: [
        {"key": "telegram", "label": "Telegram", "emoji": "\U0001f4f1"},
    ])
    monkeypatch.setattr(gateway_mod, "_configure_platform", lambda platform: None)
    monkeypatch.setattr(gateway_mod, "_platform_status", lambda platform: "configured")

    choices = iter([0, 1])  # pick the only platform, then "Done"
    monkeypatch.setattr(gateway_mod, "prompt_choice", lambda *a, **kw: next(choices))

    start_questions = []

    def _prompt_yes_no(question, default=None):
        if "start" in question.lower():
            start_questions.append(question)
            return not decline_start
        return default

    monkeypatch.setattr(gateway_mod, "prompt_yes_no", _prompt_yes_no)
    return start_questions


def test_declined_start_is_not_reasked_after_platform_setup(monkeypatch, capsys):
    start_questions = _patch_common(monkeypatch, decline_start=True)

    gateway_mod.gateway_setup()

    assert len(start_questions) == 1, (
        f"the 'start the (installed but stopped) gateway service' question was asked "
        f"{len(start_questions)} times in one run, expected 1: {start_questions}"
    )
