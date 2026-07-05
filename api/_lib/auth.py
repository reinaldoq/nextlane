import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .errors import api_error
from .settings import env

_bearer = HTTPBearer(auto_error=False)

# Mission Control is an INTERNAL operator/ops console (agent runs, USD cost, PR
# links), not a dealer-facing screen -- so its routes are gated to an operator
# allowlist rather than every authenticated dealer. `OPERATOR_EMAILS` is a
# comma-separated env allowlist; empty/unset means "no operators" (deny by
# default). The web mirrors this by only showing the Mission Control nav/route
# to operators (see `GET /api/me`).
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


def _operator_emails() -> set[str]:
    """The operator allowlist from `OPERATOR_EMAILS` (comma-separated,
    case-insensitive). Unset/empty => empty set => nobody is an operator."""
    return {part.strip().lower() for part in env("OPERATOR_EMAILS", "").split(",") if part.strip()}


def is_operator(user: dict) -> bool:
    """Whether the authenticated user's email is on the operator allowlist."""
    return (user.get("email") or "").strip().lower() in _operator_emails()


def require_operator(user: dict = Depends(current_user)) -> dict:
    """Like `current_user`, but 403s unless the user is an operator. Gates the
    internal Mission Control routes so ordinary dealers can't read agent-run
    ops data (cost, PR links, internal tooling)."""
    if not is_operator(user):
        raise api_error(403, "forbidden", "operator access required")
    return user
