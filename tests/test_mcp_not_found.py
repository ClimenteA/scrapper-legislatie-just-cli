"""Anticipated MCP lookup misses must be clean tool errors, not crash logs.

Clients sometimes send an article number or node id that is not in the index.
The SDK logs bare exceptions at ERROR with a full traceback and hides the
message from the model. These tests make sure we use its deliberate-failure
channel instead: a ``ToolError`` with an actionable message for the model and
a single WARNING line for the operator.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from loguru import logger
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from scrapper_legislatie_just import mcp_http
from scrapper_legislatie_just.bm25_index import Bm25IndexBuilder
from scrapper_legislatie_just.query import LegalIndex

FIXTURES = Path(__file__).parent / "fixtures" / "needle"


@pytest.fixture(autouse=True)
def patched_index(tmp_path, monkeypatch):
    root = tmp_path / "legislatie-descarcata"
    shutil.copytree(FIXTURES / "3-expanded", root / "3-expanded")
    Bm25IndexBuilder(surse=[], data_dir=root).build()

    def build() -> LegalIndex:
        return LegalIndex(data_dir=root)

    monkeypatch.setattr(mcp_http, "_index", build)
    return root


def test_missing_article_is_actionable_tool_error():
    with pytest.raises(ToolError) as exc:
        mcp_http.article("act-a", "9999")
    message = str(exc.value)
    assert "9999" in message
    assert "Articole disponibile" in message  # helps the model retry with a real number
    assert not isinstance(exc.value, UnexpectedToolError)  # would log a traceback


def test_unknown_full_id_is_actionable_tool_error():
    with pytest.raises(ToolError) as exc:
        mcp_http.show("act-a@2024-01-01#art-9999")
    assert "9999" in str(exc.value)


def test_invalid_node_id_is_actionable_tool_error():
    with pytest.raises(ToolError) as exc:
        mcp_http.show("not-a-full-id")
    assert "ID invalid" in str(exc.value)


def test_valid_full_id_still_works():
    result = mcp_http.show("act-a@2024-01-01#art-2")
    assert "Exceptii de la exceptie" in result


def test_misses_emit_warning():
    records: list[tuple[str, str]] = []
    sink_id = logger.add(
        lambda msg: records.append(
            (msg.record["level"].name, msg.record["message"])
        ),
        level="WARNING",
    )
    try:
        with pytest.raises(ToolError):
            mcp_http.article("act-a", "9999")
    finally:
        logger.remove(sink_id)

    assert [level for level, _ in records] == ["WARNING"]
    assert "MCP tool 'article'" in records[0][1]
