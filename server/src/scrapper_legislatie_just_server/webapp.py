"""FastAPI host for the hosted (stateless) MCP server.

Lives in the independent ``scrapper-legislatie-just-server`` package, which
depends on the shared ``scrapper-legislatie-just-cli`` package (the index,
pipeline and CLI).

Endpoints:

* ``POST /mcp``          — Streamable HTTP MCP, bearer-token protected
* ``POST /admin/tokens`` — issue a signed token (admin_secret_key in body)
* ``POST /admin/reindex`` — optional manual trigger for the index rebuild
* ``GET  /health``       — liveness + index presence

The personal-file tools are deliberately NOT exposed here: this service keeps
no user data on disk. Local users keep using the stdio server from
``scrapper_legislatie_just.mcp_server`` with ``date-context/`` and
``raspunsuri-ai/``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel

from scrapper_legislatie_just.config import resolve_data_dir, step_dir

from .auth import check_admin_secret, issue_token, parse_expiry, verify_token
from .mcp_http import mcp
from .settings import get_settings, get_web_settings


def _transport_security() -> TransportSecuritySettings:
    settings = get_settings()
    if settings.mcp_disable_dns_rebinding:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return TransportSecuritySettings(
        allowed_hosts=settings.mcp_allowed_hosts,
        allowed_origins=settings.mcp_allowed_origins,
    )


# Build the sub-app at import time: mcp.session_manager only exists after
# streamable_http_app() has been called, and the host lifespan needs it.
mcp_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    transport_security=_transport_security(),
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # WebSettings validation fails fast if the secrets are missing or invalid.
    get_web_settings()
    async with mcp.session_manager.run():
        yield


app = FastAPI(
    title="Legislatie Just MCP",
    description="Hosted, stateless MCP over the Romanian legal index.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def bearer_auth(request: Request, call_next) -> Response:
    """Gate the MCP endpoint with the signed bearer token."""
    if request.url.path.rstrip("/").startswith("/mcp"):
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not token:
            return JSONResponse(
                {"detail": "Missing bearer token. Use Authorization: Bearer <token>."},
                status_code=401,
            )
        try:
            verify_token(token)
        except ValueError as ex:
            return JSONResponse({"detail": str(ex)}, status_code=401)
    return await call_next(request)


class TokenRequest(BaseModel):
    user_email: str
    token_expire_date: str
    admin_secret_key: str


class ReindexRequest(BaseModel):
    admin_secret_key: str
    force: bool = False
    ocr: bool = True


@app.post("/admin/tokens", tags=["admin"])
def create_token(payload: TokenRequest) -> dict[str, Any]:
    """Issue a signed token. The raw secret must match ADMIN_SECRET_KEY in .env."""
    if not check_admin_secret(payload.admin_secret_key):
        raise HTTPException(status_code=401, detail="Invalid admin secret key.")
    try:
        expires_at = parse_expiry(payload.token_expire_date)
        token = issue_token(payload.user_email, expires_at)
    except ValueError as ex:
        raise HTTPException(status_code=422, detail=str(ex)) from ex
    return {
        "token": token,
        "user_email": payload.user_email,
        "expires_at": expires_at.isoformat(),
    }


@app.post("/admin/reindex", tags=["admin"])
def manual_reindex(payload: ReindexRequest) -> dict[str, Any]:
    """Trigger the same rebuild the monthly scheduler runs."""
    if not check_admin_secret(payload.admin_secret_key):
        raise HTTPException(status_code=401, detail="Invalid admin secret key.")
    from scrapper_legislatie_just.reindex import reindex

    result = reindex(force=payload.force, ocr=payload.ocr)
    return {"status": "ok", **result}


@app.get("/health", tags=["ops"])
def health() -> dict[str, Any]:
    root = resolve_data_dir()
    return {
        "status": "ok",
        "index": (step_dir(root, "processed") / "law.db").exists(),
        "graph": (step_dir(root, "processed") / "graph.db").exists(),
    }


# The MCP sub-app owns the /mcp route itself. Mounting at "" avoids the
# /mcp -> /mcp/ redirect that a Mount("/mcp") would introduce, while the
# routes declared above still win because they are registered first.
app.mount("", mcp_app)


def main() -> None:
    """Console entrypoint: uvicorn with multiple workers."""
    settings = get_settings()
    uvicorn.run(
        "scrapper_legislatie_just_server.webapp:app",
        host=settings.web_host,
        port=settings.web_port,
        workers=settings.web_workers,
    )
