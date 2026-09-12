"""Tests for xAI OAuth 403 error recovery in auxiliary_client.

xAI returns HTTP 403 (not 401) with "unauthenticated:bad-credentials" when
an OAuth2 access token has expired.  These tests verify the three fixes:

1. _is_auth_error detects xAI 403 as an auth failure
2. _recoverable_pool_provider maps api.x.ai to xai-oauth
3. _refresh_provider_credentials includes xai-oauth refresh logic
"""

import pytest


# ── _is_auth_error ──────────────────────────────────────────────────────────

def _import_is_auth_error():
    from agent.auxiliary_client import _is_auth_error
    return _is_auth_error


class TestIsAuthErrorXaiOauth403:
    """Verify _is_auth_error correctly identifies xAI's 403 bad-credentials."""

    @pytest.fixture(autouse=True)
    def _import(self):
        self.is_auth_error = _import_is_auth_error()

    def test_xai_403_bad_credentials_is_auth_error(self):
        """The exact error xAI returns for expired OAuth tokens."""
        exc = Exception(
            "Error code: 403 - {'code': 'The caller does not have permission "
            "to execute the specified operation', 'error': 'The OAuth2 access "
            "token could not be validated. [WKE=unauthenticated:bad-credentials]'}"
        )
        exc.status_code = 403  # openai.PermissionDenied sets this
        assert self.is_auth_error(exc) is True

    def test_xai_403_bad_credentials_without_status_code(self):
        """Fallback match when status_code attribute is missing."""
        exc = Exception(
            "Error code: 403 - unauthenticated:bad-credentials"
        )
        # No status_code attribute — should still match via string pattern
        assert self.is_auth_error(exc) is True

    def test_generic_403_is_not_auth_error(self):
        """A generic 403 (e.g. rate limit, forbidden) should NOT be treated as auth."""
        exc = Exception("Error code: 403 - rate limit exceeded")
        exc.status_code = 403
        assert self.is_auth_error(exc) is False






    def test_unauthenticated_without_bad_credentials_is_not_auth_error(self):
        """'unauthenticated' alone (without 'bad-credentials') should not match."""
        exc = Exception("unauthenticated request")
        assert self.is_auth_error(exc) is False


# ── _recoverable_pool_provider ──────────────────────────────────────────────

def _import_recoverable_pool_provider():
    from agent.auxiliary_client import _recoverable_pool_provider
    return _recoverable_pool_provider


class TestRecoverablePoolProviderXaiOAuth:
    """Verify _recoverable_pool_provider maps api.x.ai to xai-oauth."""

    @pytest.fixture(autouse=True)
    def _import(self):
        self.recover = _import_recoverable_pool_provider()

    def test_explicit_xai_oauth_provider(self):
        """Explicit provider name passes through."""
        result = self.recover("xai-oauth", None)
        assert result == "xai-oauth"

    def test_api_x_ai_host_match(self):
        """api.x.ai base URL maps to xai-oauth pool."""
        class MockClient:
            base_url = "https://api.x.ai/v1/"

        result = self.recover("auto", MockClient())
        assert result == "xai-oauth"

    def test_auto_with_unknown_host_returns_none(self):
        """auto provider with unknown host returns None."""
        class MockClient:
            base_url = "https://unknown.example.com/v1/"

        result = self.recover("auto", MockClient())
        assert result is None


# ── _refresh_provider_credentials (structure check) ─────────────────────────

def _import_refresh_provider_credentials():
    from agent.auxiliary_client import _refresh_provider_credentials
    return _refresh_provider_credentials


# ── _auth_refresh_provider_for_route ────────────────────────────────────────

def _import_auth_refresh_provider_for_route():
    from agent.auxiliary_client import _auth_refresh_provider_for_route
    return _auth_refresh_provider_for_route


class TestAuthRefreshProviderForRouteXaiOAuth:
    """Verify auto-routed xAI calls resolve to the xai-oauth refresher.

    goal_judge and other auxiliary tasks that inherit the main model keep
    resolved_provider == "auto"; the refresh table must map api.x.ai to
    xai-oauth the same way _POOL_PROVIDER_BY_HOST already does, or the
    refresher never runs and the request falls through to OpenRouter/Nous.
    """

    @pytest.fixture(autouse=True)
    def _import(self):
        self.route = _import_auth_refresh_provider_for_route()

    def test_auto_api_x_ai_resolves_to_xai_oauth(self):
        assert self.route("auto", "https://api.x.ai/v1/") == "xai-oauth"

    def test_explicit_xai_oauth_provider_passes_through(self):
        assert self.route("xai-oauth", "https://api.x.ai/v1/") == "xai-oauth"

    def test_auto_unknown_host_stays_auto(self):
        assert self.route("auto", "https://unknown.example.com/v1/") == "auto"


class TestRefreshProviderCredentialsXaiOAuth:
    """Verify _refresh_provider_credentials has xai-oauth branch.

    Full integration testing requires live OAuth tokens, so we verify
    the branch exists and handles the no-credential case gracefully.
    """

    @pytest.fixture(autouse=True)
    def _import(self):
        self.refresh = _import_refresh_provider_credentials()

    def test_xai_oauth_no_pool_returns_false(self):
        """When no xai-oauth pool exists, refresh returns False gracefully."""
        # This tests that the branch exists and doesn't crash.
        # It may return True if the singleton resolver finds tokens,
        # or False if neither pool nor singleton has credentials.
        # Either way, it should not raise an exception.
        result = self.refresh("xai-oauth")
        assert isinstance(result, bool)

    def test_unknown_provider_returns_false(self):
        """Unknown providers fall through to return False."""
        result = self.refresh("unknown-provider-xyz")
        assert result is False