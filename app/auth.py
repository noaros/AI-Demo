"""Who is asking: validates the Entra ID access token on each request.

Every endpoint except /healthz needs `Authorization: Bearer <token>`, a token Entra ID issued to a signed-in
user for this API (scope Helpdesk.Chat). The user's identity comes from the token, which Entra ID signed.
It never comes from the request body or from anything the model says.
"""

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import load_settings

settings = load_settings()
SCOPE = "Helpdesk.Chat"
AGENT_ROLE = "Helpdesk.Agent"

# Entra ID's public signing keys, fetched once and cached. A token signed by anyone else is rejected.
signing_keys = jwt.PyJWKClient(f"https://login.microsoftonline.com/{settings.auth_tenant_id}/discovery/v2.0/keys")
bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class User:
    id: str  # Entra ID object id: stable, unique, never reused
    username: str  # sign-in name, like alex.kim@contoso.com
    name: str
    roles: frozenset[str]

    @property
    def is_agent(self) -> bool:
        return AGENT_ROLE in self.roles


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
    if credentials is None:
        raise HTTPException(401, "Sign in: send an Entra ID access token as 'Authorization: Bearer <token>'",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        token = credentials.credentials
        claims = jwt.decode(
            token,
            signing_keys.get_signing_key_from_jwt(token).key,
            algorithms=["RS256"],
            audience=settings.auth_client_id,  # issued for this API, not for some other app
            issuer=f"https://login.microsoftonline.com/{settings.auth_tenant_id}/v2.0",  # by our tenant
        )  # also checks the signature and expiry
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"Invalid token: {e}", headers={"WWW-Authenticate": "Bearer"}) from e
    if SCOPE not in claims.get("scp", "").split():
        raise HTTPException(403, f"Token lacks the {SCOPE} scope")
    return User(
        id=claims["oid"],
        username=claims.get("preferred_username", "").lower(),
        name=claims.get("name", ""),
        roles=frozenset(claims.get("roles", [])),
    )
