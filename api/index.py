from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse

app = FastAPI(title="Nextlane DMS API", docs_url=None, redoc_url=None)


@app.exception_handler(HTTPException)
async def flatten_error(request: Request, exc: HTTPException):
    body = (
        exc.detail
        if isinstance(exc.detail, dict)
        else {"code": "error", "message": str(exc.detail), "details": {}}
    )
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def flatten_validation(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "message": "invalid request",
            "details": {"errors": exc.errors()},
        },
    )


@app.get("/api/health")
def health():
    return {"status": "ok"}
