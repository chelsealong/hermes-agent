"""Tests for cron.required_skills — config-enforced required skills on cron jobs.

Covers the create/update gate in tools/cronjob_tools.py: a job's `skills[]`
must cover every entry in `cron.required_skills` unless the job is
`no_agent`, or `cron.required_skills_enforce` downgrades the block to a
warning. See issue: config-enforced required skills for cron jobs.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def hermes_env(tmp_path, monkeypatch):
    """Isolate HERMES_HOME for each test so jobs/scripts don't leak."""
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "scripts").mkdir()
    (home / "cron").mkdir()

    monkeypatch.setenv("HERMES_HOME", str(home))

    import importlib
    import hermes_constants
    importlib.reload(hermes_constants)
    import cron.jobs
    importlib.reload(cron.jobs)
    import cron.scheduler
    importlib.reload(cron.scheduler)

    return home


def _patch_required(monkeypatch, required, enforce=True):
    monkeypatch.setattr(
        "hermes_cli.config.load_config_readonly",
        lambda: {"cron": {"required_skills": required, "required_skills_enforce": enforce}},
    )


def test_create_blocked_when_required_skill_missing(hermes_env, monkeypatch):
    from tools.cronjob_tools import cronjob

    _patch_required(monkeypatch, ["cron-output"])
    result = json.loads(
        cronjob(action="create", prompt="Summarize the news", schedule="every 1h")
    )
    assert result["success"] is False
    assert "cron-output" in result["error"]


def test_create_allowed_when_required_skill_present(hermes_env, monkeypatch):
    from tools.cronjob_tools import cronjob

    _patch_required(monkeypatch, ["cron-output"])
    result = json.loads(
        cronjob(
            action="create",
            prompt="Summarize the news",
            skills=["cron-output"],
            schedule="every 1h",
        )
    )
    assert result["success"] is True


def test_default_config_has_no_required_skills(hermes_env, monkeypatch):
    """No cron.required_skills configured (the real default) -> no enforcement."""
    from tools.cronjob_tools import cronjob

    monkeypatch.setattr(
        "hermes_cli.config.load_config_readonly",
        lambda: {"cron": {}},
    )
    result = json.loads(
        cronjob(action="create", prompt="Summarize the news", schedule="every 1h")
    )
    assert result["success"] is True


def test_no_agent_job_exempt(hermes_env, monkeypatch):
    from tools.cronjob_tools import cronjob

    (hermes_env / "scripts" / "watch.sh").write_text("#!/bin/bash\necho ok\n")
    _patch_required(monkeypatch, ["cron-output"])
    result = json.loads(
        cronjob(
            action="create",
            script="watch.sh",
            no_agent=True,
            schedule="every 1h",
            deliver="local",
        )
    )
    assert result["success"] is True


def test_enforce_false_downgrades_to_warning(hermes_env, monkeypatch):
    from tools.cronjob_tools import cronjob

    _patch_required(monkeypatch, ["cron-output"], enforce=False)
    result = json.loads(
        cronjob(action="create", prompt="Summarize the news", schedule="every 1h")
    )
    assert result["success"] is True


def test_update_clearing_skills_blocked(hermes_env, monkeypatch):
    from tools.cronjob_tools import cronjob

    _patch_required(monkeypatch, [])
    created = json.loads(
        cronjob(
            action="create",
            prompt="Summarize the news",
            skills=["cron-output"],
            schedule="every 1h",
        )
    )
    job_id = created["job_id"]

    _patch_required(monkeypatch, ["cron-output"])
    result = json.loads(cronjob(action="update", job_id=job_id, skills=[]))
    assert result["success"] is False
    assert "cron-output" in result["error"]


def test_update_unrelated_field_still_checks_effective_skills(hermes_env, monkeypatch):
    """A job created before the config key existed gets caught on its next
    (unrelated) edit, matching the base_url re-validate-on-every-update
    pattern already used elsewhere in cronjob_tools.py."""
    from tools.cronjob_tools import cronjob

    _patch_required(monkeypatch, [])
    created = json.loads(
        cronjob(action="create", prompt="Summarize the news", schedule="every 1h")
    )
    job_id = created["job_id"]

    _patch_required(monkeypatch, ["cron-output"])
    result = json.loads(cronjob(action="update", job_id=job_id, name="Renamed"))
    assert result["success"] is False
    assert "cron-output" in result["error"]


def test_update_adding_required_skill_succeeds(hermes_env, monkeypatch):
    from tools.cronjob_tools import cronjob

    _patch_required(monkeypatch, [])
    created = json.loads(
        cronjob(action="create", prompt="Summarize the news", schedule="every 1h")
    )
    job_id = created["job_id"]

    _patch_required(monkeypatch, ["cron-output"])
    result = json.loads(
        cronjob(action="update", job_id=job_id, skills=["cron-output"])
    )
    assert result["success"] is True
