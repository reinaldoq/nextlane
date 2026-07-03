from fastapi import HTTPException


def api_error(
    status: int,
    code: str,
    message: str,
    details: dict | None = None,
    headers: dict | None = None,
):
    return HTTPException(
        status,
        {"code": code, "message": message, "details": details or {}},
        headers=headers,
    )
