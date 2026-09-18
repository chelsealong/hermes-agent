"""An out-of-tree provider registered AFTER hermes_cli.auth seeds its registry stays selectable.

Provider discovery (``providers._discover_providers``) latches its re-entrancy flag before its
filesystem steps run, so a module that imports ``hermes_cli.auth`` while discovery is in progress
seeds ``PROVIDER_REGISTRY`` from a partial ``list_providers()`` snapshot: a profile a later
discovery step registers reaches ``providers._REGISTRY`` but never the auth gate, and
``resolve_provider`` raised "Unknown provider" for it. This reproduces that state directly — the
profile is registered only after auth has already seeded — and asserts resolution self-heals.
See #114548.
"""

from __future__ import annotations

from providers import register_provider
from providers.base import ProviderProfile


class _LateACPProfile(ProviderProfile):
    def create_client(self, **kwargs):
        return object()

    def fetch_models(self, **kwargs):
        return None


def test_provider_registered_after_auth_seeding_still_resolves():
    # Importing auth seeds PROVIDER_REGISTRY. Register the profile only afterwards so the
    # snapshot auth captured cannot contain it — exactly the partial-snapshot condition.
    from hermes_cli.auth import PROVIDER_REGISTRY, resolve_provider

    register_provider(
        _LateACPProfile(
            name="late-acp",
            aliases=("late",),
            display_name="Late ACP",
            base_url="acp://late",
            auth_type="external_process",
            process_command="/bin/true",
            process_args=(),
        )
    )

    # Precondition: the profile reached providers._REGISTRY but not the auth known-provider gate.
    assert "late-acp" not in PROVIDER_REGISTRY

    # Without the fix both raise AuthError("Unknown provider 'late-acp'."); with it they self-heal.
    assert resolve_provider("late-acp") == "late-acp"
    assert resolve_provider("late") == "late-acp"  # alias resolves too
