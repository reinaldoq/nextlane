"""`GET /api/me` -- the authenticated caller's identity + role, so the web can
decide what to show. Right now that's just the operator flag, which gates the
Mission Control nav link and route (dealers don't see the internal ops console;
see `auth.require_operator`/`OPERATOR_EMAILS`). Read-only, behind `current_user`.
"""

from fastapi import APIRouter, Depends

from .auth import current_user, is_operator

router = APIRouter()


@router.get("/me")
def me(user: dict = Depends(current_user)):
    """The caller's email and whether they're an operator. Any authenticated
    user may call this (it only ever reports on themselves)."""
    return {"email": user.get("email"), "is_operator": is_operator(user)}
