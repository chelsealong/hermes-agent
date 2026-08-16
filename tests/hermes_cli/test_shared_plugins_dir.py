"""Tests for the cross-profile ``plugins.shared_dir`` fallback (issue #87238).

Per-directory profiles (``HERMES_HOME`` overrides) each get their own
isolated ``plugins/``, so infrastructure plugins installed in one profile are
invisible in every other one. ``plugins.shared_dir`` lets a profile whose own
``plugins/`` is missing or empty fall back to a directory shared across
profiles, without disturbing profiles that already have their own plugins.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from hermes_cli.plugins import PluginManager, get_effective_user_plugins_dir


def _write_plugin(root: Path, name: str) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "version": "0.1.0", "description": f"Test plugin {name}"}
    (plugin_dir / "plugin.yaml").write_text(yaml.dump(manifest))
    (plugin_dir / "__init__.py").write_text("def register(ctx):\n    pass\n")
    return plugin_dir


def _write_config(hermes_home: Path, plugins_cfg: dict) -> None:
    cfg_path = hermes_home / "config.yaml"
    cfg: dict = {}
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
    cfg.setdefault("plugins", {}).update(plugins_cfg)
    cfg_path.write_text(yaml.safe_dump(cfg))


class TestGetEffectiveUserPluginsDir:
    def test_falls_back_to_shared_dir_when_profile_plugins_missing(self, tmp_path):
        hermes_home = Path(os.environ["HERMES_HOME"])
        shared_dir = tmp_path / "shared-plugins"
        shared_dir.mkdir()
        _write_config(hermes_home, {"shared_dir": str(shared_dir)})

        assert get_effective_user_plugins_dir() == shared_dir

    def test_prefers_profile_plugins_when_populated(self, tmp_path):
        hermes_home = Path(os.environ["HERMES_HOME"])
        profile_plugins = hermes_home / "plugins"
        _write_plugin(profile_plugins, "local-only")
        shared_dir = tmp_path / "shared-plugins"
        shared_dir.mkdir()
        _write_config(hermes_home, {"shared_dir": str(shared_dir)})

        assert get_effective_user_plugins_dir() == profile_plugins

    def test_no_shared_dir_configured_returns_profile_dir(self):
        hermes_home = Path(os.environ["HERMES_HOME"])
        assert get_effective_user_plugins_dir() == hermes_home / "plugins"


class TestDiscoveryUsesSharedDir:
    def test_plugin_installed_once_loads_in_empty_profile(self, tmp_path):
        """A plugin installed only in the shared dir loads for a profile
        whose own plugins/ was never populated — the acceptance criterion
        from #87238 ("install once, visible from any per-directory profile")."""
        hermes_home = Path(os.environ["HERMES_HOME"])
        shared_dir = tmp_path / "shared-plugins"
        _write_plugin(shared_dir, "turbofit")
        _write_config(hermes_home, {"shared_dir": str(shared_dir), "enabled": ["turbofit"]})

        mgr = PluginManager()
        mgr.discover_and_load()

        assert "turbofit" in mgr._plugins
        assert mgr._plugins["turbofit"].enabled is True

    def test_existing_profile_plugins_still_work_unaffected_by_shared_dir(self, tmp_path):
        hermes_home = Path(os.environ["HERMES_HOME"])
        _write_plugin(hermes_home / "plugins", "profile-local")
        shared_dir = tmp_path / "shared-plugins"
        _write_plugin(shared_dir, "turbofit")
        _write_config(
            hermes_home,
            {"shared_dir": str(shared_dir), "enabled": ["profile-local", "turbofit"]},
        )

        mgr = PluginManager()
        mgr.discover_and_load()

        assert "profile-local" in mgr._plugins
        assert "turbofit" not in mgr._plugins
