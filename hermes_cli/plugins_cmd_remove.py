"""``hermes plugins remove``: tree + install-metadata removal kept consistent, config bookkeeping, and the
dashboard/TUI remove path.

Sibling of :mod:`hermes_cli.plugins_cmd` (the facade re-exports the names other modules use and is
imported late here, never at module level).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any


def _pc():
    """The facade, read at call time: tests patch ``plugins_cmd.<name>`` and sibling calls must see it."""
    from hermes_cli import plugins_cmd
    return plugins_cmd


def _remove_plugin_core(target: Path) -> None:
    """Remove one plugin and its metadata without splitting their state."""
    if target.name not in _pc()._read_install_metadata():
        _pc().rmtree_readonly(target)
        return
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.remove-", dir=target.parent))
    backup = staging / "plugin"
    os.replace(target, backup)
    try:
        _pc()._update_install_record(target.name, lambda _current: None)
    except Exception:
        try:
            os.replace(backup, target)
        except OSError as restore_exc:
            raise _pc().PluginOperationError(
                f"Plugin metadata update failed and '{target.name}' could not be "
                f"restored automatically; recovery copy remains at {backup}."
            ) from restore_exc
        _pc().rmtree_readonly(staging, ignore_errors=True)
        raise
    _pc().rmtree_readonly(staging)


def _forget_stale_plugin_record(name: str, console) -> bool:
    """A plugin whose directory is already gone (e.g. manual ``rm -rf``) but whose
    install-metadata record still names it: clear that bookkeeping so a later ``hermes plugins
    install <name>`` starts clean instead of tripping the "active plugin" consent gate against a
    target it can never publish over.

    Gated on install-metadata alone (never on a bare ``plugins.enabled``/``disabled`` mention):
    only a downloaded plugin ever gets an install-metadata record, so its presence is the one
    signal that distinguishes "a download whose tree was deleted by hand" from a bundled or
    entry-point plugin's ordinary, permanent, directory-less state — those routinely sit in
    ``plugins.disabled`` (e.g. a user-disabled bundled backend) with no tree under the user
    plugins dir at all, and reconciling on that alone would silently clear the disable and
    reactivate them. Returns False when there is no install-metadata record for *name* (the
    caller reports the ordinary "no plugin named ..." error)."""
    if name not in _pc()._read_install_metadata():
        return False
    _pc()._update_install_record(name, lambda _current: None)
    result = _pc()._forget_plugin_config(_pc()._plugin_aliases(name))
    console.print()
    console.print(
        f"[yellow]⚠[/yellow] Plugin [bold]{name}[/bold]'s directory was already gone; "
        "cleared its stale install record."
    )
    if result.get("cleared_memory_provider"):
        console.print("[yellow]memory.provider pointed at this plugin and was reset; "
                      "run `hermes memory setup` to pick another.[/yellow]")
    console.print()
    return True


def cmd_remove(name: str) -> None:
    """Remove an installed plugin by name."""
    console = _pc()._console()
    plugins_dir = _pc()._plugins_dir()
    try:
        target = _pc()._sanitize_plugin_name(name, plugins_dir, allow_subdir=True)
    except ValueError as e:
        _pc()._fail(console, f"[red]Error:[/red] {e}")
    if not target.exists():
        if _forget_stale_plugin_record(name, console):
            return
        _pc()._fail(console, _pc()._unknown_plugin_message(name, downloaded_only=True))
    try:
        result = _remove_user_plugin(plugins_dir, name, target)
    except (OSError, _pc().PluginOperationError) as exc:
        _pc()._fail(console, f"[red]Error:[/red] Could not remove plugin '{name}': {exc}")
    console.print()
    console.print(f"[red]✗[/red] Plugin [bold]{name}[/bold] removed from {plugins_dir}")
    if result.get("cleared_memory_provider"):
        console.print("[yellow]memory.provider pointed at this plugin and was reset; "
                      "run `hermes memory setup` to pick another.[/yellow]")
    console.print()


def _remove_user_plugin(plugins_dir: Path, name: str, target: Path) -> dict[str, Any]:
    """Shared ``remove`` tail for the CLI, the dashboard and the ``plugins.manage`` RPC.

    *target* is the resolved directory; when ``plugins_dir/name`` itself is a symlink only the link
    goes — the tree it points at may be another installed plugin (a dev alias to a sibling checkout),
    and following it deleted that plugin plus its install metadata while the alias stayed dangling.
    Config bookkeeping (aliases, toolset) is gathered before the tree disappears.
    """
    link = plugins_dir / name.strip("/")
    if link.is_symlink():
        link.unlink()
        return {"ok": True, "name": name, **_pc()._forget_plugin_config({link.name})}
    entry = next((e for e in _pc()._discover_all_plugins() if Path(str(e[4])) == target), None)
    key = entry[5] if entry else target.name
    aliases = _pc()._plugin_aliases(key) | {target.name}
    if _pc()._read_manifest(target).get("provides_tools"):
        _pc()._toggle_plugin_toolset(key, enable=False)
    _remove_plugin_core(target)
    return {"ok": True, "name": name, **_pc()._forget_plugin_config(aliases)}


def dashboard_remove_user_plugin(name: str) -> dict[str, Any]:
    """Delete a plugin tree under ``~/.hermes/plugins/`` only."""
    plugins_dir = _pc()._plugins_dir()
    if any(n == name and src == "bundled" for n, _ver, _d, src, _path, _key in _pc()._discover_all_plugins()):
        return {"ok": False, "error": "Bundled plugins cannot be removed from the dashboard."}
    target = _pc()._user_installed_plugin_dir(name)
    if target is None:
        return {"ok": False, "error": f"Plugin '{name}' was not found under {plugins_dir}."}
    try:
        return _remove_user_plugin(plugins_dir, name, target)
    except (OSError, _pc().PluginOperationError) as exc:
        return {"ok": False, "error": f"Could not remove plugin '{name}': {exc}"}
