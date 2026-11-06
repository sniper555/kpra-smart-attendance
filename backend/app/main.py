import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import browser_origins, get_settings
from app.errors import AppError
from app.routers import admin, attendance, auth, corrections, health, timenex_map

logger = logging.getLogger("kpra")

settings = get_settings()
app = FastAPI(
    title="KPRA Smart Attendance Lite",
    docs_url=None if settings.app_env == "production" else "/api/docs",
    redoc_url=None,
    openapi_url=None if settings.app_env == "production" else "/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=browser_origins(settings),
    allow_origin_regex=r"https://[A-Za-z0-9.-]+(?::\d+)?$" if settings.app_env == "development" else None,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)

@app.get("/health")
def live() -> dict:
    return {"status": "ok"}


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(attendance.router)
app.include_router(attendance.status_router)
app.include_router(corrections.router)
app.include_router(admin.router)
app.include_router(timenex_map.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    request_id = request.headers.get("x-request-id", uuid.uuid4().hex)
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request failed")
        response = JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL", "message": "Something went wrong. Please try again."}},
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Request-Id"] = request_id
    return response


@app.exception_handler(AppError)
async def app_error(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    messages: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error.get("loc", []) if part not in {"body", "query"})
        if error.get("type") == "extra_forbidden":
            messages.append("The request includes a field that is not allowed.")
        elif loc == "reason" and error.get("type") == "string_too_short":
            messages.append("A reason of at least 10 characters is required.")
        elif loc:
            messages.append(f"{loc} is not valid.")
        else:
            messages.append("Some fields are missing or not valid.")
    unique = list(dict.fromkeys(messages))
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR", "message": " ".join(unique[:4])}},
    )


@app.exception_handler(Exception)
async def unhandled(_request: Request, _exc: Exception) -> JSONResponse:
    logger.exception("unhandled error")
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL", "message": "Something went wrong. Please try again."}},
    )
