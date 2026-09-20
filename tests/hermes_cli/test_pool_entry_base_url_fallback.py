"""Regression coverage for issue #116800.

A credential-pool entry created without a ``base_url`` (e.g. via
``hermes auth add opencode-zen``, which stores ``base_url=None``) must
still resolve to the provider's registry default endpoint. Before the
fix, ``_pool_entry_mode_and_url`` only applied the registry default
when the entry's base_url was a non-empty string that already equalled
it — an empty/``None`` base_url fell through untouched, so the runtime
resolved with ``base_url=""`` and ``switch_model`` aborted with
"no base_url resolved for provider ...".
"""

from __future__ import annotations

from hermes_cli import runtime_provider as rp


class TestPoolEntryEmptyBaseUrlFallsBackToRegistryDefault:
    def test_pool_entry_mode_and_url_fills_empty_base_url(self):
        api_mode, base_url = rp._pool_entry_mode_and_url(
            "opencode-zen", entry=None, model_cfg={}, effective_model="glm-5.3", base_url="",
        )
        assert base_url == rp.PROVIDER_REGISTRY["opencode-zen"].inference_base_url

    def test_resolve_runtime_from_pool_entry_fills_empty_base_url(self):
        class _Entry:
            access_token = "sk-opencode-zen-pool"
            runtime_api_key = "sk-opencode-zen-pool"
            source = "manual:api_key"
            base_url = None

        resolved = rp._resolve_runtime_from_pool_entry(
            provider="opencode-zen",
            entry=_Entry(),
            requested_provider="opencode-zen",
            model_cfg={"provider": "opencode-zen"},
        )

        assert resolved["base_url"] == rp.PROVIDER_REGISTRY["opencode-zen"].inference_base_url
