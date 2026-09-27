"""Tests for live auto-decompose settings resolution (issue #49638).

The gateway dispatcher used to capture ``kanban.auto_decompose`` once at boot,
so a user who flipped it to ``false`` to STOP runaway auto-decompose (which had
created and launched tasks they didn't intend) found the flag had no effect
without a full gateway restart. ``_resolve_auto_decompose_settings`` is now
called every tick, reading the current config.
"""

from __future__ import annotations


from gateway.kanban_watchers_common import _resolve_auto_decompose_settings




def test_disabled_when_flag_false():
    enabled, per_tick, max_age_days = _resolve_auto_decompose_settings(
        lambda: {"kanban": {"auto_decompose": False}}
    )
    assert enabled is False


def test_max_age_days_unset_by_default():
    _enabled, _per_tick, max_age_days = _resolve_auto_decompose_settings(
        lambda: {"kanban": {}}
    )
    assert max_age_days is None


def test_max_age_days_read_from_config():
    _enabled, _per_tick, max_age_days = _resolve_auto_decompose_settings(
        lambda: {"kanban": {"auto_decompose_max_age_days": 7}}
    )
    assert max_age_days == 7
