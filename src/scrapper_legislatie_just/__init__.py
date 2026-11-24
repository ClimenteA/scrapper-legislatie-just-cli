"""Legislatie Just — HTTP MCP server over the Romanian legal index.

A single package containing the download/clean/expand/process pipeline, the
BM25 query layer, the FastAPI application (``webapp``) and the Streamable
HTTP MCP tool layer (``mcp_http``). The only supported way to run it is the
root ``docker-compose.yml``.
"""

__version__ = "0.1.0"
