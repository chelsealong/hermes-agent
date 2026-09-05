"""Regression tests for issue #103738: Desktop/TUI drops a custom provider's
``extra_body`` (e.g. a proxy-required ``user`` field) because
``_custom_provider_extra_body_for_agent`` only matched ``provider == "custom"``
or ``"custom:<name>"``. Desktop/TUI routinely builds the agent with an empty
provider, ``"auto"``, or the catalog name without a ``custom:`` prefix, even
though ``base_url`` still points at the configured custom endpoint — CLI
worked because it always resolves a ``custom``/``custom:<name>`` provider
string first.
"""

from agent.agent_init import _custom_provider_extra_body_for_agent

BASE = "https://proxy.example/v1"

ENTRY = {
    "name": "my-proxy",
    "base_url": BASE,
    "model": "some-model",
    "extra_body": {"user": "abc123"},
}


def test_empty_provider_matches_by_base_url():
    got = _custom_provider_extra_body_for_agent(
        provider="", model="some-model", base_url=BASE, custom_providers=[ENTRY],
    )
    assert got == {"user": "abc123"}


def test_auto_provider_matches_by_base_url():
    got = _custom_provider_extra_body_for_agent(
        provider="auto", model="some-model", base_url=BASE, custom_providers=[ENTRY],
    )
    assert got == {"user": "abc123"}


def test_unprefixed_provider_name_matches():
    got = _custom_provider_extra_body_for_agent(
        provider="my-proxy", model="some-model", base_url=BASE, custom_providers=[ENTRY],
    )
    assert got == {"user": "abc123"}


def test_blank_session_model_still_yields_extra_body():
    got = _custom_provider_extra_body_for_agent(
        provider="", model="", base_url=BASE, custom_providers=[ENTRY],
    )
    assert got == {"user": "abc123"}


def test_non_matching_model_still_returns_none():
    got = _custom_provider_extra_body_for_agent(
        provider="", model="a-different-model", base_url=BASE, custom_providers=[ENTRY],
    )
    assert got is None


def test_unprefixed_name_does_not_leak_across_entries_at_same_url():
    other = {
        "name": "other-proxy",
        "base_url": BASE,
        "model": "some-model",
        "extra_body": {"user": "should-not-leak"},
    }
    got = _custom_provider_extra_body_for_agent(
        provider="my-proxy", model="some-model", base_url=BASE, custom_providers=[other],
    )
    assert got is None
