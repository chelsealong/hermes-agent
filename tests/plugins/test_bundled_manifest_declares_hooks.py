"""Bundled ``plugin.yaml`` manifests must declare hooks under ``provides_hooks``.

``PluginManager``/``hermes plugins validate`` only reads ``provides_hooks`` when
diffing declared hooks against what ``register()`` actually registers
(``hermes_cli/plugin_validate.py``); a manifest using the legacy ``hooks:`` key
is silently ignored, so every hook the plugin registers shows up as
"undeclared" (issue #108371).
"""

from pathlib import Path

import pytest

from hermes_cli.plugin_validate import validate_plugin_dir


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


# Plugins whose register() actually calls ctx.register_hook() for the hooks
# their manifest declares (as opposed to e.g. memory providers, which go
# through ctx.register_memory_provider() and never hit provides_hooks).
_AFFECTED_PLUGIN_DIRS = [
    "plugins/disk-cleanup",
    "plugins/google_meet",
    "plugins/observability/langfuse",
    "plugins/security-guidance",
]


@pytest.mark.parametrize("rel_dir", _AFFECTED_PLUGIN_DIRS)
def test_admission_validate_passes_with_no_undeclared_hooks(rel_dir):
    report = validate_plugin_dir(_repo_root() / rel_dir)
    assert report.ok, report.failures


@pytest.mark.parametrize("rel_dir", _AFFECTED_PLUGIN_DIRS)
def test_manifest_has_no_legacy_hooks_key(rel_dir):
    import yaml

    manifest = yaml.safe_load(
        (_repo_root() / rel_dir / "plugin.yaml").read_text(encoding="utf-8")
    )
    assert "hooks" not in manifest
    assert manifest.get("provides_hooks")
