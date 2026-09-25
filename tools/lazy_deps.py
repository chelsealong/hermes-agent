"""Shims to suppress old updater work until relaunch. New code must not use these."""

from typing import NoReturn


def ensure(feature: str, *, prompt: bool = True) -> NoReturn:
    # Shim to suppress old updater work until relaunch. Do not claim readiness.
    # Preserve the dependency-unavailable failure without claiming a completed install.
    raise ImportError("Dependencies are unknown to this old updater. Please relaunch Hermes.")


def install_specs(specs: list[str] | tuple[str, ...], *, timeout: int = 300) -> NoReturn:
    # Shim to suppress old updater work until relaunch. Do not install or report success.
    # Unlike hermes_cli.old_updater_deps, this module is public API that a live
    # plugin can still import and call outside any update (#122326) - it must
    # fail like `ensure` above, not call stop_for_relaunch() and start a real
    # update takeover from inside a normal hermes serve/gateway process.
    raise ImportError("Dependencies are unknown to this old updater. Please relaunch Hermes.")
