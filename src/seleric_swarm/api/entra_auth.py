"""Microsoft Entra ID bearer-token authenticator (OAuth resource server).

Plugs into ApiSecurityMiddleware's `authenticator` hook. Anything that is not a valid
Entra access token for this API returns None, so the shared API key keeps working.
"""

from __future__ import annotations

import asyncio

import jwt
from starlette.requests import Request

from seleric_swarm.conversations.contracts import Principal, PrincipalAuthMethod


def make_entra_authenticator(tenant_id: str, audience: str, *, workspace_id: str = "default"):
    # v1 and v2 endpoints issue different `iss` (and `aud`) shapes; which one an app
    # gets depends on its accessTokenAcceptedVersion, so accept both.
    issuers = [
        f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        f"https://sts.windows.net/{tenant_id}/",
    ]
    audiences = [a.strip() for a in audience.split(",") if a.strip()]
    jwks = jwt.PyJWKClient(
        f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys", cache_keys=True
    )

    async def authenticate(request: Request) -> Principal | None:
        auth = request.headers.get("authorization", "").strip()
        if not auth.lower().startswith("bearer "):
            return None
        token = auth[7:].strip()
        try:
            # PyJWKClient does blocking HTTP on a cache miss.
            key = await asyncio.to_thread(jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=audiences,
                issuer=issuers,
                options={"require": ["exp", "iss", "aud"]},
            )
        except (jwt.PyJWTError, jwt.PyJWKClientError):
            return None
        subject = claims.get("oid") or claims.get("sub")
        if not subject:
            return None
        return Principal(
            principal_id=subject,
            workspace_id=workspace_id,
            user_id=subject,
            authenticated=True,
            auth_method=PrincipalAuthMethod.OIDC,
            roles={str(r) for r in claims.get("roles", [])},
        )

    return authenticate
