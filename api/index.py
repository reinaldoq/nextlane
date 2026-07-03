from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.utils import is_body_allowed_for_status_code
from starlette.exceptions import HTTPException as StarletteHTTPException

from ._lib.events import router as events_router
from ._lib.vehicles import router as vehicles_router


async def flatten_error(request: Request, exc: StarletteHTTPException):
    headers = getattr(exc, "headers", None)
    if not is_body_allowed_for_status_code(exc.status_code):
        return Response(status_code=exc.status_code, headers=headers)
    body = (
        exc.detail
        if isinstance(exc.detail, dict)
        else {"code": "error", "message": str(exc.detail), "details": {}}
    )
    return JSONResponse(status_code=exc.status_code, content=body, headers=headers)


async def flatten_validation(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "message": "invalid request",
            "details": {"errors": jsonable_encoder(exc.errors())},
        },
    )


async def internal_error(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"code": "internal_error", "message": "internal server error", "details": {}},
    )


def register_error_handlers(application: FastAPI) -> FastAPI:
    application.add_exception_handler(StarletteHTTPException, flatten_error)
    application.add_exception_handler(RequestValidationError, flatten_validation)
    application.add_exception_handler(Exception, internal_error)
    return application


app = register_error_handlers(FastAPI(title="Nextlane DMS API", docs_url=None, redoc_url=None))
app.include_router(vehicles_router, prefix="/api")
app.include_router(events_router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}
