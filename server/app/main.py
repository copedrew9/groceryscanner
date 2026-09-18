"""FastAPI application: config, token dependency, error handlers, static mount.

Spec sections 6.1 and 6.5.
"""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import api, db

# Spec 6.1: refuse to start on a token short enough to guess.
MIN_TOKEN_LENGTH = 32


def load_api_token() -> str:
    """Read API_TOKEN, or raise so the process never comes up with an open API."""
    token = os.environ.get("API_TOKEN", "")
    if len(token) < MIN_TOKEN_LENGTH:
        raise RuntimeError(
            f"API_TOKEN must be set and at least {MIN_TOKEN_LENGTH} characters. "
            'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
        )
    return token


# Read at import time: uvicorn imports this module, so a bad token stops startup.
API_TOKEN = load_api_token()


def require_token(authorization: str = Header(default="")) -> None:
    """Spec 6.5: one dependency, constant-time compare, on every /api/ route."""
    expected = f"Bearer {API_TOKEN}"
    # Compare bytes: compare_digest rejects str containing non-ASCII, and a
    # header can carry any byte the client chose to send.
    if not hmac.compare_digest(
        authorization.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Missing or incorrect token")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # WAL and migrations run once, before the first request is served.
    db.initialize()
    yield


app = FastAPI(title="Pantry inventory", lifespan=lifespan)


# --- errors ------------------------------------------------------------------

# Spec section 4 names three codes. Clients switch on the code, never the text.
CODE_FOR_STATUS = {
    400: "invalid_request",
    401: "invalid_token",
    403: "invalid_token",
    404: "not_found",
    422: "invalid_request",
}


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


@app.exception_handler(StarletteHTTPException)
def handle_http_exception(request, exc: StarletteHTTPException) -> JSONResponse:
    """Covers raised HTTPExceptions and the 404s Starlette raises for itself."""
    code = CODE_FOR_STATUS.get(exc.status_code, "invalid_request")
    return error_response(exc.status_code, code, str(exc.detail))


@app.exception_handler(RequestValidationError)
def handle_validation_error(request, exc: RequestValidationError) -> JSONResponse:
    """Spec 6.6: FastAPI's default validation body is replaced by our shape."""
    message = "Request failed validation"
    errors = exc.errors()
    if errors:
        location = ".".join(str(part) for part in errors[0].get("loc", ()))
        detail = errors[0].get("msg", "invalid")
        message = f"{location}: {detail}" if location else detail
    return error_response(422, "invalid_request", message)


# Routes are registered before any static mount, or the mount at "/" swallows
# /api/ requests. The mount itself arrives with the web page in a later stage.
app.include_router(api.router, prefix="/api", dependencies=[Depends(require_token)])
