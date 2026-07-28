"""Minimal OAuth 2.1 authorization server for the MCP server.

Supports exactly one pre-registered client (configured via env vars), using
the authorization_code + PKCE grant with long-lived access tokens — no
refresh tokens, the simplest option for a single trusted client (e.g. the
Claude Desktop app's "Add custom connector" flow). Client-secret and PKCE
verification are handled by the mcp SDK's own /token route logic; this
provider only handles storage and issuance decisions.

The existing static MCP_API_KEY bearer token keeps working unchanged —
load_access_token accepts either a dynamically-issued OAuth token or the
static key, so Claude Code / scripts/mcp_client.py are unaffected.
"""

import os
import secrets
import time
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

AUTH_CODE_TTL_SECONDS = 300  # 5 minutes to complete the code -> token exchange


class SimpleOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """Single pre-registered client, no dynamic registration, no refresh tokens."""

    def __init__(self):
        self._codes: dict[str, AuthorizationCode] = {}
        self._tokens: dict[str, AccessToken] = {}

    def _client_id(self) -> str | None:
        return os.environ.get("OAUTH_CLIENT_ID")

    def _client_secret(self) -> str | None:
        return os.environ.get("OAUTH_CLIENT_SECRET")

    def _redirect_uri(self) -> str:
        return os.environ.get("OAUTH_REDIRECT_URI", "https://claude.ai/api/mcp/auth_callback")

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        configured_id = self._client_id()
        if not configured_id or client_id != configured_id:
            return None
        return OAuthClientInformationFull(
            client_id=configured_id,
            client_secret=self._client_secret(),
            redirect_uris=[self._redirect_uri()],
            grant_types=["authorization_code"],
            response_types=["code"],
            token_endpoint_auth_method="client_secret_post",
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        raise NotImplementedError(
            "Dynamic client registration is disabled — use the pre-registered "
            "OAUTH_CLIENT_ID/OAUTH_CLIENT_SECRET instead."
        )

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + AUTH_CODE_TTL_SECONDS,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        query = {"code": code}
        if params.state:
            query["state"] = params.state
        return f"{params.redirect_uri}?{urlencode(query)}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        record = self._codes.get(authorization_code)
        if record is None or record.client_id != client.client_id or record.expires_at < time.time():
            return None
        return record

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self._codes.pop(authorization_code.code, None)  # single use

        token = secrets.token_urlsafe(32)
        self._tokens[token] = AccessToken(
            token=token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=None,  # long-lived; no refresh flow
            resource=authorization_code.resource,
        )
        return OAuthToken(
            access_token=token,
            token_type="bearer",
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        return None  # refresh tokens are not issued — nothing to load

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        raise NotImplementedError("Refresh tokens are not supported — reconnect to obtain a new access token.")

    async def load_access_token(self, token: str) -> AccessToken | None:
        static_key = os.environ.get("MCP_API_KEY")
        if static_key and token == static_key:
            return AccessToken(token=token, client_id="static-key", scopes=[], expires_at=None)

        record = self._tokens.get(token)
        if record and (record.expires_at is None or record.expires_at > time.time()):
            return record
        return None

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self._tokens.pop(token.token, None)
