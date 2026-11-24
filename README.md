# Legislatie Just — local MCP server

Run the Legislatie Just MCP server locally in Docker and connect an MCP client
such as OpenCode to it over HTTP.

The pipeline is deterministic and local:

- the scraper downloads and cleans the HTML from `legislatie.just.ro`;
- every cleaned HTML page and every source PDF is turned into **Markdown**
  (`3-expanded/`), with `markdownify` for HTML and LiteParse + the
  `ocria-nano` OCR model for PDFs;
- the search index (`4-processed/`) is a persisted **BM25** index
  (`llama-index`), with no embeddings and no language model.

Diacritics are stripped on write, so sources and questions share the same
alphabet; user questions are folded with `unidecode`.

## Requirements

- Docker with the `docker compose` plugin.
- The repository data folder `legislatie-descarcata/`, with the source PDFs
  (`0-pdfs/`) and either the built index (`4-processed/`) or `REINDEX_ON_START=1`
  for the first build.

## Start the server

Create `.env` from the example and set the two required secrets:

```bash
cp .env.example .env
```

```dotenv
ADMIN_SECRET_KEY=choose-a-secret-for-issuing-tokens
TOKEN_SIGNING_SECRET=replace-with-a-long-random-value
```

Build the index once (the first run also downloads the ~60 MB `ocria-nano`
model, into the bind-mounted `legislatie-descarcata/models/`):

```bash
REINDEX_ON_START=1 docker compose up -d legislatie-indexer
```

Then start the app from the repository root:

```bash
docker compose up -d
```

The MCP endpoint is `http://localhost:8000/mcp`. Check that the app is
available at <http://localhost:8000/health> (it reports the BM25 index,
`docs.json` and `actions.json`). The Markdown corpus is served under
`http://localhost:8000/laws/3-expanded/`, and the source PDFs under
`http://localhost:8000/pdfs/`.

## Citations: act name, slug, version

The corpus is Markdown, so there are no HTML anchors to link to. Every source
is cited by **act name + slug + consolidation version**, for example
`Codul fiscal (codul-fiscal), versiunea 2026-10-25`. ANAF form sheets keep
their public `/pdfs/...` link, returned by the `actions` tool.

## Get an MCP token

Open <http://localhost:8000/docs>, expand `POST /admin/tokens`, and select
**Try it out**. Submit your email, an ISO 8601 expiration date, and the same
`ADMIN_SECRET_KEY` value configured in `.env`:

```json
{
  "user_email": "you@example.com",
  "token_expire_date": "2027-01-01",
  "admin_secret_key": "choose-a-secret-for-issuing-tokens"
}
```

Copy the returned token; it is shown only once. MCP clients authenticate with
it using the `Authorization: Bearer <token>` header.

## Connect with OpenCode

Configure OpenCode with a remote HTTP MCP server at
`http://localhost:8000/mcp` and set its `Authorization` header to
`Bearer <token copied from /docs>`. Open the repository in OpenCode and verify
the connection with `opencode mcp list` or `/mcps` in the TUI. Then ask a
question about Romanian legislation; the client can use the `legislatie_*` MCP
tools (`search`, `article`, `show`, `context`, `select_docs`, `research`,
`actions`, `convert_currency`, `validate_answer`).

For another MCP client, configure a remote/HTTP server at
`http://localhost:8000/mcp` and pass the same bearer-token header.

## Rebuild the index

The indexer service runs the pipeline on a schedule and also supports a
one-shot rebuild. Set `REINDEX_ON_START=1` in `.env` (or on the command line)
and restart the `legislatie-indexer` service:

```bash
docker compose restart legislatie-indexer
```

## Stop the server

```bash
docker compose down
```
