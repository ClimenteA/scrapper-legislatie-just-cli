# Legislatie Just — MCP server

A single Docker service that serves a local index of Romanian legislation
(`legislatie.just.ro`) as an **HTTP MCP server**. OpenCode, Claude Code,
Codex, VS Code and other MCP clients connect to it over the network with a
bearer token.

There is no `curl` installer, no `install.sh` / `install.ps1`, no local Python
environment and no stdio server. Everything — the download/clean/expand/process
pipeline, the query and graph layer, and the MCP tools — runs in the container.
Your only local command is `docker compose up -d`.

## Quick start

### 1. Prerequisites

- Docker with the `docker compose` plugin.
- The data folder `legislatie-descarcata/` at the repository root. It must
  contain `4-processed/law.db` and `4-processed/graph.db`, plus the HTML in
  `3-expanded/`. If the index is not built yet, set `REINDEX_ON_START=1` in
  `.env` for the first launch (see [Reindexing](#reindexing)).
- Optionally the ANAF/EU PDF corpus inside the data folder, at
  `legislatie-descarcata/0-pdfs/` (`declaratii-pdf/` and `eu-pdfs/`).

No Python, uv or `monolith` installation is needed on the host: the images
contain everything, including `monolith` for downloads.

### 2. Configure

All deployment files (`Dockerfile`, `docker-compose.yml`, `.env.example` and
your `.env`) are at the repository root. Compose reads `.env` automatically.

```bash
cp .env.example .env
# edit .env — at minimum change ADMIN_SECRET_KEY and TOKEN_SIGNING_SECRET
```

```dotenv
ADMIN_SECRET_KEY=the-admin-secret-key-change-it
TOKEN_SIGNING_SECRET=<long-random-value>   # python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`ADMIN_SECRET_KEY` is the plain string you send when issuing a token.
`TOKEN_SIGNING_SECRET` signs the issued JWTs; **rotating it revokes every
token at once**. Neither value is written to disk by the app.

### 3. Start

Run every `docker compose` command from the repository root, so Compose finds
`.env` and resolves the relative bind mounts.

```bash
docker compose up -d
# or: make start
```

The root `Makefile` wraps the common Compose commands: `make start`,
`make stop`, `make build` and `make logs` (follow logs with Ctrl-C to exit).

Two services start:

| Service | Purpose |
|---|---|
| `legislatie-app` | FastAPI + Streamable HTTP MCP on `localhost:8000` |
| `legislatie-indexer` | APScheduler process for the monthly rebuild |

The data folder (including `0-pdfs/`) is bind-mounted into both containers, so
the index stays on your host and is visible from both sides. Check the app:

```bash
curl http://localhost:8000/health
# {"status":"ok","index":true,"graph":true}
```

### 4. Get a token from the OpenAPI docs

Open **<http://localhost:8000/docs>**, expand `POST /admin/tokens`, click
**Try it out** and send:

```json
{
  "user_email": "you@example.com",
  "token_expire_date": "2027-01-01T23:59:59+02:00",
  "admin_secret_key": "the-admin-secret-key-change-it"
}
```

`token_expire_date` is ISO 8601. Times without an offset are interpreted as
**Europe/Bucharest**; `Z` and explicit offsets are respected; past dates are
rejected with `422`. The response contains the signed JWT — copy it. It is
returned once and not stored anywhere.

Equivalent from the terminal:

```bash
curl -X POST http://localhost:8000/admin/tokens \
  -H 'Content-Type: application/json' \
  -d '{"user_email":"you@example.com","token_expire_date":"2027-01-01T23:59:59+02:00","admin_secret_key":"the-admin-secret-key-change-it"}'
```

### 5. Connect your AI agent

All clients connect to the same endpoint:

```text
http://localhost:8000/mcp
Authorization: Bearer <token>
```

Keep `codemode: false` where the client supports it, so the native
`legislatie_*` tool names used by the instructions file are preserved.

#### OpenCode

This repository already ships a working `opencode.json`. Export the token and
open the repository root in OpenCode:

```bash
export MCP_API_KEY='<token copied from /docs>'
```

```jsonc
// opencode.json
{
  "$schema": "https://opencode.ai/config.json",
  "instructions": ["legislatie-descarcata/4-processed/AGENTS-cloud.md"],
  "mcp": {
    "servers": {
      "legislatie": {
        "type": "remote",
        "url": "http://localhost:8000/mcp",
        "oauth": false,
        "codemode": false,
        "headers": { "Authorization": "Bearer {env:MCP_API_KEY}" }
      }
    }
  }
}
```

You can also add it globally with `opencode mcp add legislatie --url
http://localhost:8000/mcp --header "Authorization: Bearer <token>"` (check
`opencode mcp add --help` for the exact flags in your version) and verify with
`opencode mcp list` or `/mcps`.

#### Claude Code

```bash
claude mcp add --scope user --transport http \
  --header "Authorization: Bearer <token>" \
  legislatie -- http://localhost:8000/mcp
claude mcp list
```

Tools appear as `mcp__legislatie__search`, `mcp__legislatie__context`, etc.

#### Codex CLI

Edit `~/.codex/config.toml`:

```toml
[mcp_servers.legislatie]
url = "http://localhost:8000/mcp"
bearer_token_env_var = "MCP_API_KEY"
startup_timeout_sec = 30
```

#### GitHub Copilot in VS Code

Create `.vscode/mcp.json`:

```json
{
  "servers": {
    "legislatie": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "headers": { "Authorization": "Bearer ${input:mcp_token}" }
    }
  },
  "inputs": [
    { "id": "mcp_token", "type": "promptString", "description": "MCP token" }
  ]
}
```

#### Other clients (Kilo, Cursor, Windsurf, Gemini CLI, Zed, Claude Desktop)

Use the portable `mcpServers` / `servers` remote-HTTP form with the same
`url` and `Authorization` header. Zed uses `context_servers` with a `url`
field. The tool prefix varies by client (`legislatie_search` in OpenCode and
Kilo, `mcp__legislatie__search` in Claude Code).

### 6. Check the connection and ask a question

```bash
opencode mcp list      # or /mcps in the TUI
curl http://localhost:8000/health
```

Then ask in Romanian, e.g. `În Codul muncii, câte zile de concediu de odihnă
am?`. The assistant calls `legislatie_context` / `legislatie_search`, reads
the article with `legislatie_article`, follows citations with
`legislatie_neighbors` and cites the exact anchor returned by the tool.

The answering rules are generated at index time into
`legislatie-descarcata/4-processed/AGENTS-cloud.md`; `opencode.json` already
points at it. Other clients look for `AGENTS.md`, `CLAUDE.md`,
`.github/copilot-instructions.md`, etc. — point them at `AGENTS-cloud.md`
(the remote version, without the personal-file tools).

## What the server exposes

Endpoints:

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /mcp` | Bearer token | Streamable HTTP MCP (stateless) |
| `POST /admin/tokens` | `admin_secret_key` in body | Issue a signed token |
| `GET /health` | none | Liveness + `law.db` / `graph.db` presence |
| `GET /docs` | none | OpenAPI UI (issue your token here) |

Tools (all read-only; the server stores no user data):

| Tool | Purpose |
|---|---|
| `search` | BM25 search over the whole corpus, with a link to the exact anchor |
| `article` | One article with all paragraphs, letters and notes |
| `show` | One node (article, paragraph, letter, note) with parents and citations |
| `neighbors` | Incoming/outgoing citations; `subtree=true` covers the article |
| `context` | Ready-to-read evidence pack for a question |
| `docs` | Indexed acts and their consolidation dates |
| `cypher` | Cypher query over `4-processed/graph.db` |
| `convert_currency` | Foreign amount → RON with the BNR rate and effective date |
| `actions` | ANAF declaration sheets (form code, description, PDF link) |
| `answer_template` | Mandatory answer format |
| `graph_visualization` | Link to the generated Docling graph viewer |

Personal-file tools from the old local stdio server (`context_files`,
`read_context`, `get_user_tax_context`, `save_user_tax_context`, `save_answer`)
do **not** exist here: the web image never reads PDFs and writes no files.
Your fiscal context and saved answers stay in your own client, not on the
server.

## Configuration

Set these in `.env` (Compose passes them to the container; variables
not set here fall back to the defaults in
`src/scrapper_legislatie_just/settings.py`).

| Variable | Default | Purpose |
|---|---|---|
| `ADMIN_SECRET_KEY` | — (required) | Plain secret accepted by `/admin/tokens` |
| `TOKEN_SIGNING_SECRET` | — (required, ≥16 chars) | JWT signing key; rotate to revoke all tokens |
| `PUBLIC_LAWS_BASE_URL` | `http://localhost:8000/laws` | Prefix of every result link (see below) |
| `MCP_ALLOWED_HOSTS` | `localhost`, `127.0.0.1` (+ `:port`) | MCP DNS-rebinding allowlist |
| `MCP_ALLOWED_ORIGINS` | `http://localhost:8000` | Allowed browser origins |
| `MCP_DISABLE_DNS_REBINDING` | `0` | Set `1` only when a trusted proxy owns `Host` |
| `WEB_WORKERS` | `2` | uvicorn worker processes |
| `REINDEX_ON_START` | `0` | `1` builds the index when `make start` launches the indexer |
| `REINDEX_OCR` | `1` | OCR of base64 images during reindex (indexer only) |
| `SCRAPPER_DATA_DIR` | `/data/legislatie-descarcata` | Data root inside the container |
| `SCRAPPER_PDF_SOURCES_DIR` | `/data/legislatie-descarcata/0-pdfs` | PDF corpus root (inside the data folder) |
| `TZ` | `Europe/Bucharest` | Container timezone |

### Result links and `PUBLIC_LAWS_BASE_URL`

Every link returned by a tool is built from `PUBLIC_LAWS_BASE_URL`:
HTML acts become `<base>/3-expanded/...#anchor`, PDF sources become
`<root>/pdfs/...#page=N` (where `<root>` is `<base>` without `/laws`).

The base URL is **yours to configure**. The local default points at
`http://localhost:8000/laws`, which is only meaningful once something serves
those static files. No Caddy configuration is shipped in this repository: add
a reverse proxy yourself and set `PUBLIC_LAWS_BASE_URL` to its public URL (for
example `https://your-host/laws`). Until you do, treat the returned links as
exact source identifiers rather than clickable URLs. Add any proxy hostname to
`MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` as well.

## Reindexing

The indexer container runs APScheduler with a cron trigger for the **15th of
each month at 03:00 Europe/Bucharest**, using the same pipeline as a full
rebuild. It runs in its own container so web workers never block, and a file
lock prevents overlapping runs. `law.db` and `graph.db` are written to
temporary files and published with `os.replace`, so readers always see a
complete index.

OCR (`docling`, `easyocr`, `docling-graph`, with torch/CUDA) is a **required
dependency**, not an optional extra. The pipeline uses it for base64 images
embedded in downloaded HTML and for PDF ingestion, so the single image shared
by both containers includes it (and is correspondingly large, ~5 GB).

To rebuild on startup, set `REINDEX_ON_START=1` in `.env` before running
`make start`. Compose passes it only to the `legislatie-indexer` service, which
runs the rebuild once before entering the scheduler loop. It defaults to `0`
(disabled). Set it back to `0` after the first successful build so later
`make start` calls do not rebuild again; otherwise leave it at `1` to force a
rebuild on every start.

## Adding Caddy yourself

No Caddy service or `Caddyfile` is included. When you are ready to expose the
server, put your own reverse proxy in front and route as follows:

- `/mcp`, `/admin/*`, `/health`, `/docs`, `/openapi.json` → `legislatie-app:8000`;
- `/laws/*` → read-only `3-expanded/`;
- `/pdfs/*` → read-only `5-pdfs/`;
- `/graph/*` → read-only `4-processed/graph-docling/`.

Only the expanded HTML and PDFs should be public — never expose `law.db` or
`graph.db`. Then set `PUBLIC_LAWS_BASE_URL=https://your-host/laws`,
`MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS` accordingly in `.env` and
recreate the app container (`docker compose up -d --force-recreate
legislatie-app`). Clients keep the same MCP path and token; only the base URL
in returned links changes.

## Where the files are

Everything is bind-mounted from the repository root:

```text
legislatie-descarcata/
├── 0-pdfs/       ANAF declarations + EU acts (input for the PDF corpus)
├── 1-raw/        downloaded HTML
├── 2-clean/      cleaned HTML
├── 3-expanded/   HTML, Markdown and TOC JSON per act (public via /laws)
├── 4-processed/  law.db, graph.db, AGENTS-cloud.md, answer-format.md
└── 5-pdfs/       generated PDF copies (public via /pdfs)
```

Final answers are no longer written by the server — save them in your client.

## Custom sources

The packaged source list is
`src/scrapper_legislatie_just/sources.toml`. To index your own acts, add
`[[sources]]` entries there (or mount a file and set `SCRAPPER_SOURCES_FILE`
on the indexer) and rebuild the index:

```toml
[[sources]]
numar_link = 1576
nume = "Legea contabilitatii nr. 82/1991"
slug = "legea-contabilitatii"
descriere = "Stabileste principiile de baza ale contabilitatii."
```

`numar_link` is the number at the end of
`https://legislatie.just.ro/Public/DetaliiDocument/<numar_link>`. Removing a
source and reindexing deletes its files from the pipeline folders.

## The citation graph and Cypher

`law.db` holds text and the FTS5/BM25 index; `graph.db` holds the citation
graph of the same corpus. Nodes are the structural elements (`Act`, `Titlu`,
`Capitol`, `Articol`, `Alineat`, `Litera`, `Nota`, ...); edges are `CITEAZA` /
`MENTIONEAZA` (exact nodes) and their rolled-up `*_ARTICOL` variants carrying
a `count`. No Neo4j server is involved: GraphQLite implements Cypher over
SQLite.

Direction matters: `(a)-[r]->(b)` with `CITEAZA` means "a cites b";
`(a)<-[r]-(b)` means "b cites a". Example queries for `legislatie_cypher`:

```cypher
// Articles that cite Codul fiscal art. 60
MATCH (a:Articol)-[r:CITEAZA_ARTICOL]->(b:Articol {doc: 'codul-fiscal', number: '60'})
RETURN a.number AS articol, r.count AS citari
ORDER BY citari DESC
```

```cypher
// Most cited articles in the whole corpus
MATCH (a:Articol)-[r:CITEAZA_ARTICOL]->(b:Articol)
RETURN b.doc AS act, b.number AS articol, count(r) AS citari
ORDER BY citari DESC
LIMIT 10
```

GraphQLite does not understand `--` or `/* */` comments, so strip them before
running a query. The graph stores structure, not text: use `search` or
`context` for wording, and `neighbors` / `show` for one or two hops around a
known article.

## Stopping and cleaning up

```bash
make stop                    # or: docker compose down — keeps host data
docker compose down -v       # also remove the compose network
```

There is no uninstall script and no `uv tool` environment to remove. To start
fresh, delete the `legislatie-descarcata/` folder from the repository root and
recreate it with `REINDEX_ON_START=1`.

## Common problems

- **`401 Missing bearer token` / `Invalid or expired token`** — issue a new
  token at `/docs` and make sure your client sends
  `Authorization: Bearer <token>` and that the token has not expired.
- **`401 Invalid admin secret key`** — the `admin_secret_key` in your
  `/admin/tokens` request must exactly match `ADMIN_SECRET_KEY` in `.env`.
  Recreate the app container after editing `.env`.
- **`Invalid host header` on `/mcp`** — the hostname you use must be in
  `MCP_ALLOWED_HOSTS` (or set `MCP_DISABLE_DNS_REBINDING=1` behind a trusted
  proxy). Recreate the container after changing it.
- **Every tool call says the index is missing** — build it with
  `REINDEX_ON_START=1` before `make start` (or wait for the scheduler). Confirm
  `curl http://localhost:8000/health` reports `index: true` and `graph: true`.
- **Result links do not open** — they come from `PUBLIC_LAWS_BASE_URL`. Serve
  `3-expanded/` / `5-pdfs/` (e.g. with your own Caddy) and point that variable
  at it; see [Result links](#result-links-and-public_laws_base_url).
- **A tool appears missing in OpenCode** — the MCP connection is established
  when the session starts. Restart OpenCode after changing the token or the
  server, then run `opencode mcp list`.

## Development / tests

From a source checkout, `uv sync` installs the single package, its test
dependencies and the required OCR stack (the first sync downloads torch and is
large); `uv run pytest` then runs the whole suite without any extra flag. Some
tests are skipped when the real corpus is not built. The Docker image is the
only supported runtime — there is no stdio server to run locally.

## License

See [LICENSE](LICENSE).
