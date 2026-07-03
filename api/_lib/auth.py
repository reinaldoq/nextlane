import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .errors import api_error
from .settings import env

_bearer = HTTPBearer(auto_error=False)
_jwk_clients: dict[str, jwt.PyJWKClient] = {}

_UNAUTHENTICATED_HEADERS = {"WWW-Authenticate": "Bearer"}


def _client() -> jwt.PyJWKClient:
    url = env("SUPABASE_JWKS_URL")
    if url not in _jwk_clients:
        _jwk_clients[url] = jwt.PyJWKClient(url, cache_keys=True, timeout=3)
    return _jwk_clients[url]


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    if creds is None:
        raise api_error(
            401, "unauthenticated", "missing bearer token", headers=_UNAUTHENTICATED_HEADERS
        )
    try:
        key = _client().get_signing_key_from_jwt(creds.credentials)
        return jwt.decode(
            creds.credentials,
            key.key,
            algorithms=["ES256"],
            audience="authenticated",
            issuer=env("SUPABASE_JWT_ISSUER"),
            leeway=30,
            options={"require": ["exp", "aud", "iss", "sub"]},
        )
    except jwt.PyJWKClientConnectionError as e:
        # JWKS endpoint unreachable is our outage, not the caller's bad token.
        raise api_error(
            503, "service_unavailable", "auth keys unavailable", headers={"Retry-After": "5"}
        ) from e
    except jwt.PyJWTError as e:
        raise api_error(
            401,
            "unauthenticated",
            f"invalid token: {type(e).__name__}",
            headers=_UNAUTHENTICATED_HEADERS,
        ) from e
