"""FastAPI host for the (stateless) HTTP MCP server.

This is the single entry point of the application: the index, pipeline and
tool code all live in the same ``scrapper_legislatie_just`` package. The only
supported way to run it is the root ``docker-compose.yml``.

Endpoints:

* ``POST /mcp``          — Streamable HTTP MCP, bearer-token protected
* ``POST /admin/tokens`` — issue a signed token (admin_secret_key in body)
* ``GET  /health``       — liveness + index presence
* ``GET  /laws/3-expanded/*`` — public expanded legal Markdown
* ``GET  /pdfs/*``        — public source PDFs (ANAF forms, EU acts)

The personal-file tools are deliberately NOT exposed here: the service keeps
no user data on disk and exposes only the read-only, stateless legal tools.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel

from scrapper_legislatie_just.config import resolve_data_dir, step_dir
from scrapper_legislatie_just.logger import log

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
    # Pull the OCR model once, into the persistent data directory. A network
    # failure must not keep the API down: PDF ingestion is retried by the
    # indexer, while text queries keep working from the existing index.
    try:
        from scrapper_legislatie_just.pdf_convert import ensure_ocr_model

        await asyncio.to_thread(ensure_ocr_model)
    except Exception as ex:  # noqa: BLE001 - startup must survive offline hosts
        log.warning(f"Modelul LiteParse nu a putut fi pregatit la pornire: {ex}")
    async with mcp.session_manager.run():
        yield


app = FastAPI(
    title="Legislatie Just MCP",
    description="Hosted, stateless MCP over the Romanian legal index.",
    version="1.0.0",
    lifespan=lifespan,
)


# Serve the public legal sources alongside the MCP API. The corpus is mounted
# read-only from the app's perspective: clients can only issue GET/HEAD requests
# through Starlette's StaticFiles implementation.
data_root = resolve_data_dir()
expanded_dir = step_dir(data_root, "expanded")
pdfs_dir = data_root / "5-pdfs"
app.mount(
    "/laws/3-expanded",
    StaticFiles(directory=expanded_dir, check_dir=False),
    name="laws-expanded",
)
app.mount(
    "/pdfs",
    StaticFiles(directory=pdfs_dir, check_dir=False),
    name="laws-pdfs",
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


@app.get("/health", tags=["ops"])
def health() -> dict[str, Any]:
    root = resolve_data_dir()
    processed = step_dir(root, "processed")
    return {
        "status": "ok",
        "index": (processed / "bm25").exists(),
        "documents": (processed / "docs.json").exists(),
        "actions": (processed / "actions.json").exists(),
    }


# The MCP sub-app owns the /mcp route itself. Mounting at "" avoids the
# /mcp -> /mcp/ redirect that a Mount("/mcp") would introduce, while the
# routes declared above still win because they are registered first.
app.mount("", mcp_app)


def main() -> None:
    """Console entrypoint: uvicorn with multiple workers."""
    settings = get_settings()
    uvicorn.run(
        "scrapper_legislatie_just.webapp:app",
        host=settings.web_host,
        port=settings.web_port,
        workers=settings.web_workers,
    )
