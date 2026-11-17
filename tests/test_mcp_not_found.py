"""Anticipated MCP lookup misses must be clean tool errors, not crash logs.

Clients sometimes send an article number or node id that is not in the index.
The SDK logs bare exceptions at ERROR with a full traceback and hides the
message from the model. These tests make sure we use its deliberate-failure
channel instead: a ``ToolError`` with an actionable message for the model and
a single WARNING line for the operator.
"""

from __future__ import annotations

import re

import pytest
from loguru import logger
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from scrapper_legislatie_just.query import LegalIndex
from scrapper_legislatie_just_server import mcp_http


def _some_article_id() -> str:
    index = LegalIndex()
    try:
        row = index.connection.execute(
            "SELECT id FROM nodes WHERE kind = 'articol' LIMIT 1"
        ).fetchone()
        return row["id"]
    finally:
        index.close()


def test_missing_article_is_actionable_tool_error():
    with pytest.raises(ToolError) as exc:
        mcp_http.article("codul-fiscal", "9999")
    message = str(exc.value)
    assert "9999" in message
    assert "Articole disponibile" in message  # helps the model retry with a real number
    assert not isinstance(exc.value, UnexpectedToolError)  # would log a traceback


def test_bare_anchor_gets_full_id_candidates():
    # ``s_art_281`` is copied from a SURSA: link; it is ambiguous across acts.
    with pytest.raises(ToolError) as exc:
        mcp_http.neighbors("s_art_281")
    message = str(exc.value)
    assert "ID complet" in message
    # a concrete full id the model can use next, whatever the current version
    assert re.search(r"\w+@\d{4}-\d{2}-\d{2}#", message)


def test_unknown_full_id_is_actionable_tool_error():
    unknown = _some_article_id().split("#")[0] + "#s_art_9999"
    with pytest.raises(ToolError) as exc:
        mcp_http.show(unknown)
    assert "nu exista in index" in str(exc.value)


def test_valid_full_id_still_works():
    result = mcp_http.neighbors(_some_article_id())
    assert "Trimiteri din nod" in result


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
            mcp_http.article("codul-fiscal", "9999")
    finally:
        logger.remove(sink_id)

    assert [level for level, _ in records] == ["WARNING"]
    assert "MCP tool 'article'" in records[0][1]
