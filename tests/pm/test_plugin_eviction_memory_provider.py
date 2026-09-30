"""Regression for #129198: evicting a home plugin must not clear a bundled memory provider."""
from __future__ import annotations

import hermes_yaml as yaml

from pm.plugin_eviction import PluginEviction


def _evict(tmp_path, monkeypatch, name: str, provider: str) -> dict:
    home = tmp_path / "home"
    plugin_dir = home / "plugins" / name
    plugin_dir.mkdir(parents=True)
    (home / "config.yaml").write_text(yaml.safe_dump({"memory": {"provider": provider}}), encoding="utf-8")
    monkeypatch.setattr("pm.publication.selection_snapshot", lambda: {})
    eviction = PluginEviction([(home / "plugins", name, plugin_dir)], {plugin_dir.resolve(): "broken"})
    (_path, _previous, proposed), = eviction.edits
    return yaml.safe_load(proposed.decode("utf-8"))


def test_bundled_memory_provider_survives_eviction_of_same_named_copy(tmp_path, monkeypatch):
    from plugins.memory import _MEMORY_PLUGINS_DIR

    bundled = next(p.name for p in _MEMORY_PLUGINS_DIR.iterdir() if (p / "__init__.py").exists())
    config = _evict(tmp_path, monkeypatch, bundled, bundled)
    assert config["memory"]["provider"] == bundled
    assert bundled in config["plugins"]["disabled"]


def test_non_bundled_memory_provider_is_cleared_on_eviction(tmp_path, monkeypatch):
    config = _evict(tmp_path, monkeypatch, "zz-not-bundled", "zz-not-bundled")
    assert config["memory"]["provider"] == ""
