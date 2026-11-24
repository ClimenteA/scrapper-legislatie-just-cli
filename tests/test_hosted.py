"""Tests for the hosted (stateless) MCP mode."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scrapper_legislatie_just.bm25_index import Bm25IndexBuilder
from scrapper_legislatie_just.query import LegalIndex
from scrapper_legislatie_just.auth import (
    check_admin_secret,
    issue_token,
    parse_expiry,
    verify_token,
)
from scrapper_legislatie_just.settings import get_settings, get_web_settings

FIXTURES = Path(__file__).parent / "fixtures" / "needle"


def sse_json(text: str) -> dict:
    """Extract the first JSON payload from a Streamable HTTP SSE response."""
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError(f"No SSE data line in response: {text[:200]}")


BASE_URL = "https://mcp.chatcodfiscal.ro/laws"
ADMIN_SECRET = "the-admin-secret-key-change-it"
SIGNING_SECRET = "a-very-long-random-signing-key-for-tests-1234567890"


@pytest.fixture(autouse=True)
def secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_SECRET_KEY", ADMIN_SECRET)
    monkeypatch.setenv("TOKEN_SIGNING_SECRET", SIGNING_SECRET)
    monkeypatch.setenv("PUBLIC_LAWS_BASE_URL", BASE_URL)
    monkeypatch.setenv("LITEPARSE_MODEL_DIR", str(tmp_path / "models"))
    # TestClient uses host "testserver", which the DNS-rebinding allowlist
    # rejects; disable it here (production enables it via the .env defaults).
    monkeypatch.setenv("MCP_DISABLE_DNS_REBINDING", "1")
    # Settings are cached; rebuild them against the patched environment.
    get_settings.cache_clear()
    get_web_settings.cache_clear()
    # The OCR model is irrelevant for text queries; never hit the network.
    monkeypatch.setattr(
        "scrapper_legislatie_just.pdf_convert.ensure_ocr_model",
        lambda *args, **kwargs: False,
    )


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "legislatie-descarcata"
    shutil.copytree(FIXTURES / "3-expanded", root / "3-expanded")
    Bm25IndexBuilder(surse=[], data_dir=root).build()
    monkeypatch.setenv("SCRAPPER_DATA_DIR", str(root))
    return root


def test_token_lifecycle():
    assert check_admin_secret(ADMIN_SECRET)
    assert not check_admin_secret("wrong")

    expiry = parse_expiry("2099-01-01T00:00:00Z")
    assert expiry.tzinfo is not None
    token = issue_token("user@example.com", expiry)
    assert verify_token(token)["sub"] == "user@example.com"

    with pytest.raises(ValueError):
        parse_expiry("2020-01-01T00:00:00Z")
    with pytest.raises(ValueError):
        parse_expiry("not-a-date")


def test_expired_token_rejected():
    from jwt import encode

    expired = encode(
        {
            "sub": "user@example.com",
            "exp": int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp()),
            "jti": "fixed",
        },
        SIGNING_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(ValueError):
        verify_token(expired)


def test_hosted_endpoints(data_root):
    # Imported here so the environment is already patched.
    from scrapper_legislatie_just.webapp import app

    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health["status"] == "ok"
        assert health["index"] is True
        assert health["documents"] is True

        bad = client.post(
            "/admin/tokens",
            json={
                "user_email": "user@example.com",
                "token_expire_date": "2099-01-01T00:00:00Z",
                "admin_secret_key": "wrong",
            },
        )
        assert bad.status_code == 401

        issued = client.post(
            "/admin/tokens",
            json={
                "user_email": "user@example.com",
                "token_expire_date": "2099-01-01T00:00:00Z",
                "admin_secret_key": ADMIN_SECRET,
            },
        )
        assert issued.status_code == 200
        token = issued.json()["token"]

        # The manual rebuild endpoint was removed; startup reindex is the only
        # explicit trigger (REINDEX_ON_START on the indexer).
        assert client.post("/admin/reindex", json={}).status_code == 404

        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        assert client.post("/mcp", json=payload).status_code == 401

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        }
        response = client.post("/mcp", headers=headers, json=payload)
        assert response.status_code == 200
        names = {
            tool["name"] for tool in sse_json(response.text)["result"]["tools"]
        }
        assert names == {
            "search",
            "article",
            "show",
            "context",
            "docs",
            "select_docs",
            "research",
            "convert_currency",
            "actions",
            "answer_template",
            "validate_answer",
        }

        call = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "search",
                    "arguments": {"query": "handicap accentuat", "limit": 1},
                },
            },
        )
        assert call.status_code == 200
        assert "file://" not in call.text
        assert "SURSA:" in call.text
        assert "`act-a`" in call.text

        markdown = client.get(
            "/laws/3-expanded/act-a/100-act-a-2024-01-01.md"
        )
        assert markdown.status_code == 200
        assert "Scutiri pentru programatori" in markdown.text


def test_local_source_line_has_no_anchor(data_root):
    index = LegalIndex(data_dir=data_root)
    try:
        hit = index.search("handicap accentuat", limit=1)[0]
        assert "#" not in hit["source"].split("https://")[0]
        assert "`act-a`" in hit["source"]
        assert "versiunea 2024-01-01" in hit["source"]
    finally:
        index.close()


def test_reindex_on_start_setting(monkeypatch):
    monkeypatch.delenv("REINDEX_ON_START", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().reindex_on_start is False
        monkeypatch.setenv("REINDEX_ON_START", "1")
        get_settings.cache_clear()
        assert get_settings().reindex_on_start is True
    finally:
        get_settings.cache_clear()


def test_no_user_folders_created_by_hosted_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPPER_DATA_DIR", str(tmp_path / "legislatie-descarcata"))
    from scrapper_legislatie_just.config import resolve_data_dir

    root = resolve_data_dir()
    assert (root / "4-processed").exists()
    assert not (tmp_path / "date-context").exists()
    assert not (tmp_path / "raspunsuri-ai").exists()
