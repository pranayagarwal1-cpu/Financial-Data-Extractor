"""Tests for the minimal OAuth 2.1 provider (SimpleOAuthProvider).

Pure logic only — no live HTTP calls. The mcp SDK's own /authorize and /token
route handlers (client-secret and PKCE verification) aren't exercised here;
this covers the provider's storage/issuance decisions in isolation.
"""

import time

import pytest

from mcp.server.auth.provider import AuthorizationCode
from mcp.shared.auth import OAuthClientInformationFull

from mcp_server.oauth_provider import SimpleOAuthProvider

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def oauth_env(monkeypatch):
    monkeypatch.setenv("OAUTH_CLIENT_ID", "test-client")
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("OAUTH_REDIRECT_URI", "https://claude.ai/api/mcp/auth_callback")


@pytest.fixture
def provider():
    return SimpleOAuthProvider()


class TestGetClient:
    async def test_returns_client_for_configured_id(self, provider):
        client = await provider.get_client("test-client")
        assert client is not None
        assert client.client_id == "test-client"
        assert client.client_secret == "test-secret"

    async def test_returns_none_for_unknown_id(self, provider):
        assert await provider.get_client("someone-else") is None

    async def test_register_client_is_disabled(self, provider):
        with pytest.raises(NotImplementedError):
            await provider.register_client(None)


class TestAuthorizeAndExchange:
    async def _get_client(self, provider):
        return await provider.get_client("test-client")

    async def test_authorize_returns_redirect_with_code_and_state(self, provider):
        client = await self._get_client(provider)
        params = _make_params(state="xyz123")

        redirect_url = await provider.authorize(client, params)

        assert redirect_url.startswith("https://claude.ai/api/mcp/auth_callback?")
        assert "code=" in redirect_url
        assert "state=xyz123" in redirect_url

    async def test_load_authorization_code_roundtrip(self, provider):
        client = await self._get_client(provider)
        params = _make_params()
        redirect_url = await provider.authorize(client, params)
        code = redirect_url.split("code=")[1].split("&")[0]

        loaded = await provider.load_authorization_code(client, code)
        assert loaded is not None
        assert loaded.client_id == "test-client"
        assert loaded.code_challenge == params.code_challenge

    async def test_load_authorization_code_rejects_wrong_client(self, provider):
        client = await self._get_client(provider)
        other_client = OAuthClientInformationFull(
            client_id="someone-else", redirect_uris=["https://example.com/cb"]
        )
        redirect_url = await provider.authorize(client, _make_params())
        code = redirect_url.split("code=")[1].split("&")[0]

        assert await provider.load_authorization_code(other_client, code) is None

    async def test_load_authorization_code_rejects_expired(self, provider):
        client = await self._get_client(provider)
        expired_code = AuthorizationCode(
            code="expired-code",
            scopes=[],
            expires_at=time.time() - 10,
            client_id="test-client",
            code_challenge="abc",
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            redirect_uri_provided_explicitly=True,
        )
        provider._codes["expired-code"] = expired_code

        assert await provider.load_authorization_code(client, "expired-code") is None

    async def test_exchange_issues_token_and_consumes_code(self, provider):
        client = await self._get_client(provider)
        redirect_url = await provider.authorize(client, _make_params())
        code = redirect_url.split("code=")[1].split("&")[0]
        auth_code = await provider.load_authorization_code(client, code)

        oauth_token = await provider.exchange_authorization_code(client, auth_code)

        assert oauth_token.access_token
        assert oauth_token.token_type == "Bearer"
        assert oauth_token.refresh_token is None
        # single-use: the code should no longer be loadable
        assert await provider.load_authorization_code(client, code) is None


class TestRefreshTokensUnsupported:
    async def test_load_refresh_token_returns_none(self, provider):
        client = await provider.get_client("test-client")
        assert await provider.load_refresh_token(client, "anything") is None

    async def test_exchange_refresh_token_raises(self, provider):
        client = await provider.get_client("test-client")
        with pytest.raises(NotImplementedError):
            await provider.exchange_refresh_token(client, None, [])


class TestLoadAccessToken:
    async def test_accepts_static_mcp_api_key(self, provider, monkeypatch):
        monkeypatch.setenv("MCP_API_KEY", "the-static-key")
        result = await provider.load_access_token("the-static-key")
        assert result is not None
        assert result.client_id == "static-key"

    async def test_rejects_unknown_token(self, provider, monkeypatch):
        monkeypatch.setenv("MCP_API_KEY", "the-static-key")
        assert await provider.load_access_token("garbage") is None

    async def test_accepts_dynamically_issued_token(self, provider):
        client = await provider.get_client("test-client")
        redirect_url = await provider.authorize(client, _make_params())
        code = redirect_url.split("code=")[1].split("&")[0]
        auth_code = await provider.load_authorization_code(client, code)
        oauth_token = await provider.exchange_authorization_code(client, auth_code)

        result = await provider.load_access_token(oauth_token.access_token)
        assert result is not None
        assert result.client_id == "test-client"


class TestRevokeToken:
    async def test_revoke_removes_token(self, provider):
        client = await provider.get_client("test-client")
        redirect_url = await provider.authorize(client, _make_params())
        code = redirect_url.split("code=")[1].split("&")[0]
        auth_code = await provider.load_authorization_code(client, code)
        oauth_token = await provider.exchange_authorization_code(client, auth_code)

        loaded = await provider.load_access_token(oauth_token.access_token)
        await provider.revoke_token(loaded)

        assert await provider.load_access_token(oauth_token.access_token) is None


def _make_params(state: str | None = "s1"):
    from mcp.server.auth.provider import AuthorizationParams
    return AuthorizationParams(
        state=state,
        scopes=[],
        code_challenge="dummy-challenge",
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        redirect_uri_provided_explicitly=True,
    )
