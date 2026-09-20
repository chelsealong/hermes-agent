"""Multiplexed gateway housekeeping (#116700): the Curator/Sync/Org-sync ticks must run once per
served profile inside that profile's own ``_profile_runtime_scope``, like the sibling
``_mcp_config_reconciler`` chore already does -- not once, unscoped, under the launch profile's home
for every served profile."""

from pathlib import Path
from types import SimpleNamespace

import gateway.run as gateway_run


def _runner(multiplex: bool):
    return SimpleNamespace(config=SimpleNamespace(multiplex_profiles=multiplex))


def test_skill_sync_runs_once_per_profile_under_its_own_home(tmp_path, monkeypatch):
    prof_a = tmp_path / "profiles" / "a"
    prof_b = tmp_path / "profiles" / "b"
    prof_a.mkdir(parents=True)
    prof_b.mkdir(parents=True)
    monkeypatch.setattr(
        gateway_run, "_multiplex_profile_homes", lambda config: [("a", prof_a), ("b", prof_b)])

    seen_homes: list = []
    monkeypatch.setattr(
        "tools.skills_sync_client.maybe_pull_skills",
        lambda: seen_homes.append(gateway_run.get_hermes_home()))

    gateway_run._housekeeping_skill_sync(_runner(multiplex=True))

    assert seen_homes == [prof_a, prof_b]


def test_org_skill_sync_runs_once_per_profile_under_its_own_home(tmp_path, monkeypatch):
    prof_a = tmp_path / "profiles" / "a"
    prof_b = tmp_path / "profiles" / "b"
    prof_a.mkdir(parents=True)
    prof_b.mkdir(parents=True)
    monkeypatch.setattr(
        gateway_run, "_multiplex_profile_homes", lambda config: [("a", prof_a), ("b", prof_b)])

    seen_homes: list = []
    monkeypatch.setattr(
        "tools.skills_sync_client_org.maybe_pull_org_skills",
        lambda: seen_homes.append(gateway_run.get_hermes_home()))

    gateway_run._housekeeping_org_skill_sync(_runner(multiplex=True))

    assert seen_homes == [prof_a, prof_b]


def test_curator_tick_runs_once_per_profile_under_its_own_home(tmp_path, monkeypatch):
    prof_a = tmp_path / "profiles" / "a"
    prof_b = tmp_path / "profiles" / "b"
    prof_a.mkdir(parents=True)
    prof_b.mkdir(parents=True)
    monkeypatch.setattr(
        gateway_run, "_multiplex_profile_homes", lambda config: [("a", prof_a), ("b", prof_b)])

    seen_homes: list = []
    monkeypatch.setattr(
        "agent.curator.maybe_run_curator",
        lambda **_kw: seen_homes.append(gateway_run.get_hermes_home()))

    gateway_run._housekeeping_curator(_runner(multiplex=True))

    assert seen_homes == [prof_a, prof_b]


def test_skill_sync_stays_unscoped_when_multiplex_is_off(tmp_path, monkeypatch):
    """Single-profile gateways: byte-for-byte the historical behavior -- one call, no home override."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    launch_home = gateway_run.get_hermes_home()

    calls: list = []
    monkeypatch.setattr(
        "tools.skills_sync_client.maybe_pull_skills",
        lambda: calls.append(gateway_run.get_hermes_home()))
    homes_called = []
    monkeypatch.setattr(
        gateway_run, "_multiplex_profile_homes",
        lambda config: homes_called.append(config) or [("default", Path("/should-not-be-used"))])

    gateway_run._housekeeping_skill_sync(_runner(multiplex=False))
    gateway_run._housekeeping_skill_sync(None)  # no runner at all (e.g. an external cron provider caller)

    assert calls == [launch_home, launch_home]
    assert homes_called == [], "multiplex profile enumeration must not run when multiplexing is off"
