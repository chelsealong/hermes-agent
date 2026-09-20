"""Regression coverage for issue #116800.

The Dashboard's "Add credential" endpoint (``POST /api/credentials/pool`` ->
``hermes_cli.web_routers.ops.add_credential_pool_entry``) builds its
``PooledCredential`` without ever passing ``base_url``, so the entry keeps
the dataclass default of ``None`` (unlike the CLI's ``hermes auth add``,
which always stamps ``_provider_base_url(provider)``). Before the fix,
``_pool_entry_mode_and_url`` only applied the registry default when the
entry's base_url was a non-empty string that already equalled it — a
``None``/empty base_url fell through untouched, so the runtime resolved
with ``base_url=""`` and ``switch_model`` aborted with
"no base_url resolved for provider ...".
"""

from __future__ import annotations

import asyncio

from hermes_cli import runtime_provider as rp


class TestPoolEntryEmptyBaseUrlFallsBackToRegistryDefault:
    def test_pool_entry_mode_and_url_fills_empty_base_url(self):
        api_mode, base_url = rp._pool_entry_mode_and_url(
            "opencode-zen", entry=None, model_cfg={}, effective_model="glm-5.3", base_url="",
        )
        assert base_url == rp.PROVIDER_REGISTRY["opencode-zen"].inference_base_url

    def test_dashboard_added_credential_resolves_registry_base_url(self):
        """Real reproduction: add via the Dashboard endpoint, then resolve a runtime from it."""
        from agent.credential_pool import load_pool
        from hermes_cli.web_models import CredentialPoolAdd
        from hermes_cli.web_routers.ops import add_credential_pool_entry

        asyncio.run(add_credential_pool_entry(
            CredentialPoolAdd(provider="opencode-zen", api_key="sk-dash-key", label="dash")))

        entry = load_pool("opencode-zen").entries()[0]
        assert entry.base_url is None  # the endpoint never records one

        resolved = rp._resolve_runtime_from_pool_entry(
            provider="opencode-zen", entry=entry, requested_provider="opencode-zen",
            model_cfg={"provider": "opencode-zen"},
        )
        assert resolved["base_url"] == rp.PROVIDER_REGISTRY["opencode-zen"].inference_base_url
