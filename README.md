# Legislatie Just CLI

Download Romanian laws from `legislatie.just.ro`, keep a local copy, and search
it offline. No account, API key, or cloud service is required. The search index
is built on your computer.

## Quick start

### 1. Install

One command. It installs [uv](https://docs.astral.sh/uv/), the CLI, and the
`monolith` binary used to save the pages.

Linux and macOS (also Git Bash, MSYS2 and WSL on Windows):

```bash
curl -LsSf https://raw.githubusercontent.com/ClimenteA/scrapper-legislatie-just-cli/main/install.sh | sh
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/ClimenteA/scrapper-legislatie-just-cli/main/install.ps1 | iex
```

Check that it works:

```bash
scrapper-legislatie-just-cli --help
monolith --version
```

The installer is safe to run again: it upgrades an existing installation.
Useful options:

| Option | Meaning |
|---|---|
| `--ref v0.1.0` | Install a tag, branch, or commit instead of `main` |
| `--upgrade` | Force a reinstall of the current version |
| `--python 3.12` | Python version for the tool environment |
| `--repo <url>` | Install from a fork, an SSH URL, or a local checkout |
| `--no-monolith` | Do not download the `monolith` binary |

When piping, pass options after `sh -s --`:

```bash
curl -LsSf .../install.sh | sh -s -- --ref v0.1.0
```

The same values work as environment variables: `SCRAPPER_REF`,
`SCRAPPER_REPO_URL`, `SCRAPPER_PYTHON`, `SCRAPPER_NO_MONOLITH=1`.

What gets installed:

| What | Where |
|---|---|
| `scrapper-legislatie-just-cli` and `scrapper-legislatie-just-mcp` | `~/.local/bin` (`%USERPROFILE%\.local\bin` on Windows) |
| Python and dependencies | uv's tool environment, managed by `uv tool` |
| Data | `./legislatie-descarcata`, in the directory where you run the commands |

Upgrade by running the installer again, or `uv tool upgrade
scrapper-legislatie-just-cli`. To remove everything, see
[Uninstall](#uninstall).

The Cypher graph (`graphqlite`) is installed by default. The OCR stack
(`docling`, `easyocr`) is an optional `ocr` extra: the one-line installer and
source checkouts below include it, because `read_context` and OCR during
`download`/`expand` need it. The first install downloads several hundred MB
(together with torch) and takes a few minutes; later runs are cached. The
hosted FastAPI **web** image intentionally **omits** this extra (the separate
indexer image needs it) — see
[Hosted (cloud) MCP with FastAPI](#hosted-cloud-mcp-with-fastapi).

#### From source (development)

Python **3.12** is the supported version (`requires-python = ">=3.12,<3.13"`
and the `.python-version` file). The Docker images also use Python 3.12.

```bash
git clone https://github.com/ClimenteA/scrapper-legislatie-just-cli.git
cd scrapper-legislatie-just-cli
uv sync --extra ocr
```

Without `--extra ocr` the CLI and the full local MCP still work (including
`--no-ocr` runs); only PDF/image reading and OCR-enabled downloads need the
extra.

The checkout is a **uv workspace** with two packages:

- the root `scrapper-legislatie-just-cli` package — the CLI, the index/pipeline
  code and the local stdio MCP server;
- `server/` — the `scrapper-legislatie-just-server` package, i.e. the hosted
  FastAPI MCP server. It depends on the CLI package with the `[server]` extra.

`uv sync` at the root installs **both** packages from the checkout (a
`[tool.uv.sources]` workspace override makes the server use the local CLI
instead of its GitHub URL). You can run the hosted server locally with
`uv run scrapper-legislatie-just-web` after setting the two secrets from
[Configuration](#1-configuration).

Every command in this README is written with the `uv run` prefix from the
checkout. If you installed with the one-liner, just drop `uv run` and call
`scrapper-legislatie-just-cli` directly.

### 2. Try one law first

This downloads one document, converts it, and builds the search index:

```bash
uv run scrapper-legislatie-just-cli run-all \
  --slug legea-contabilitatii \
  --limit 1 \
  --no-ocr
```

Use `--no-ocr` unless the document contains scanned images.

### 3. Search it

```bash
uv run scrapper-legislatie-just-cli search "inventariere"
uv run scrapper-legislatie-just-cli context "cand se face inventarierea"
uv run scrapper-legislatie-just-cli article legea-contabilitatii 7
```

Results contain a `file:///...` link. Copy it into your browser to open the
original HTML at the exact article or paragraph.

### 4. Build the full library

```bash
uv run scrapper-legislatie-just-cli run-all --no-ocr
```

The full download can take a while and `legislatie.just.ro` may rate-limit you.
If it stops, run the same command again; existing files are skipped.

## Everyday commands

| Task | Command |
|---|---|
| List available acts | `uv run scrapper-legislatie-just-cli list-sources` |
| Download and update everything | `uv run scrapper-legislatie-just-cli run-all --no-ocr` |
| Search all indexed laws | `uv run scrapper-legislatie-just-cli search "concediu de odihna"` |
| Search inside one act | `uv run scrapper-legislatie-just-cli search "concediu" --doc codul-muncii` |
| Get an evidence pack for a question | `uv run scrapper-legislatie-just-cli context "cate zile de concediu de odihna"` |
| Read one article | `uv run scrapper-legislatie-just-cli article codul-muncii 145` |
| List indexed acts | `uv run scrapper-legislatie-just-cli docs` |
| Query the citation graph | `uv run scrapper-legislatie-just-cli cypher "MATCH (a:Articol) RETURN a.doc AS act LIMIT 5"` |
| Save a final answer as Markdown | `uv run scrapper-legislatie-just-cli save-answer "cate zile de concediu am?" --answer-file raspuns.md` |
| List PDFs/images you added | `uv run scrapper-legislatie-just-cli context-files` |
| Show the persistent fiscal context | `uv run scrapper-legislatie-just-cli tax-context` |
| Read a PDF/image with OCR | `uv run scrapper-legislatie-just-cli read-context factura.pdf` |
| Convert a foreign amount to RON | `uv run scrapper-legislatie-just-cli convert 100 EUR` |
| Show data locations | `uv run scrapper-legislatie-just-cli paths` |

Use `--help` after any command to see its options.

## Reading user PDFs and screenshots

The legal index only contains laws. Documents supplied by the user (invoices,
contracts, notices, screenshots) are read separately from the `date-context/`
folder next to the data folder:

```bash
mkdir -p date-context
cp ~/Downloads/factura.pdf date-context/
uv run scrapper-legislatie-just-cli context-files
uv run scrapper-legislatie-just-cli read-context factura.pdf
```

`read-context` uses the Docling/EasyOCR stack from the optional `ocr` extra
(included by the installer and `uv sync --extra ocr`). It accepts PDF, PNG, JPG/JPEG, WEBP, TIFF/TIF, BMP and GIF. The first
conversion may download the Docling/EasyOCR models; later conversions use the
local cache. The Markdown extraction is cached in `date-context/.converted/`,
so the second read is instant. By default it exports every Docling content
layer (body, headers/footers, background, hidden text, footnotes), because
invoices and contracts often carry the IBAN, VAT number, company registration
number and address outside the main body. Useful options:

```bash
# only pages 1-3 of a long PDF
uv run scrapper-legislatie-just-cli read-context contract.pdf --pages 1-3

# redo the conversion, ignoring the cache
uv run scrapper-legislatie-just-cli read-context scan.png --force

# disable OCR for a PDF that already has a text layer
uv run scrapper-legislatie-just-cli read-context contract.pdf --no-ocr

# keep only the main body (drop page headers/footers and hidden text)
uv run scrapper-legislatie-just-cli read-context contract.pdf --body-only
```

The same functionality is exposed to AI clients as the `legislatie_context_files`
and `legislatie_read_context` MCP tools. The file path may be absolute, relative
to the current directory, a bare name inside `date-context/`, or a `file://`
URI. In OpenCode, drag the file into the chat and pass the path it gives you to
`legislatie_read_context`; or copy the file into `date-context/` and use the
bare file name. Use `$SCRAPPER_CONTEXT_DIR` or `--context-dir` to store the
files elsewhere.

## Converting foreign amounts to RON

The CLI and the MCP server convert foreign-currency amounts to RON with the BNR
reference rates, through the installed `cursvalutarbnr` package:

```bash
uv run scrapper-legislatie-just-cli convert 1250.50 EUR
uv run scrapper-legislatie-just-cli convert 100 USD --date 2024-08-02
```

The output shows the converted amount, the applied rate (`1 EUR = 4.9753 RON`)
and the effective rate date. BNR does not publish rates on weekends and
holidays, so the closest published rate is used and the output names the date
that was actually applied. Downloaded rates are cached on disk, so repeated
conversions work offline.

In an AI client the same conversion is the `legislatie_convert_currency` MCP
tool, with `amount`, `currency` and an optional `date` (`YYYY-MM-DD`). It is
useful for invoices or contracts that contain amounts in a foreign currency.

## What each step does

| Step | Command | What it does |
|---|---|---|
| 1 | `download` | Downloads the latest printable HTML from `legislatie.just.ro` |
| 2 | `clean` | Cleans the HTML and makes it readable |
| 3 | `expand` | Creates one folder per act with HTML, Markdown, and a table of contents |
| 4 | `process` | Builds `law.db` (search index) and `graph.db` (citation graph, queried with Cypher) |

`run-all` runs all four steps in order.

By default `download` keeps only the most recent consolidation for each source:
older local versions are removed from `1-raw`, `2-clean` and `3-expanded`, and
the processed index is invalidated until the next `process`. Use
`--all-versions` to keep the full consolidation history.

Some acts have no consolidation history on `legislatie.just.ro`. For them the
download date is used in the file name, so `download` also compares a
normalized SHA-256 of the saved HTML with the existing versions of the same
act. If the content is unchanged, the new file is discarded instead of adding
a duplicate.

## Using it with an AI assistant

The steps below describe the **local stdio** mode. If you want to host the MCP
over HTTP for users who only have OpenCode, see
[Two ways to use the MCP server](#two-ways-to-use-the-mcp-server) below.

After `process`, the folder `legislatie-descarcata/4-processed/` contains the
index and a generated `AGENTS.md` with the answering rules: search the index
first, cite the exact HTML anchor, check the cited exceptions, never answer
from memory.

Those rules only work if the assistant has the `legislatie_*` tools. The stdio
MCP server in this repository exposes them, and every client below runs the
same command. Configure it once, then ask legal questions normally.

### 1. Build the index

```bash
uv run scrapper-legislatie-just-cli process
```

The server reads `4-processed/law.db` and `4-processed/graph.db`. Without them
every tool call returns an error.

### 2. Note the absolute paths

If you installed with the one-liner, the server is the
`scrapper-legislatie-just-mcp` command. Editors do not see your shell `PATH`, so
use the absolute path:

```bash
command -v scrapper-legislatie-just-mcp   # e.g. /home/you/.local/bin/scrapper-legislatie-just-mcp
echo "$PWD/legislatie-descarcata"
```

From a source checkout the same server runs from the virtualenv:

```bash
echo "$PWD/.venv/bin/python"   # then: -m scrapper_legislatie_just.mcp_server
echo "$PWD/legislatie-descarcata"
```

Every snippet below uses these placeholders:

| Placeholder | Meaning |
|---|---|
| `/abs/path/scrapper-legislatie-just-cli` | repository root (source checkout) |
| `/abs/path/scrapper-legislatie-just-cli/.venv/bin/python` | interpreter with the package installed (source checkout) |
| `scrapper-legislatie-just-mcp` | installed server command (one-line install) |
| `/abs/path/scrapper-legislatie-just-cli/legislatie-descarcata` | data folder |

The snippets use the source-checkout form. With the one-liner, replace the pair

```json
"command": "/abs/path/....venv/bin/python",
"args": ["-m", "scrapper_legislatie_just.mcp_server"]
```

with a single command and no `args`:

```json
"command": "/home/you/.local/bin/scrapper-legislatie-just-mcp"
```

On Windows that is
`%USERPROFILE%\.local\bin\scrapper-legislatie-just-mcp.exe`, and the checkout
interpreter is `...\.venv\Scripts\python.exe`; escape the backslashes inside
JSON and TOML strings (`C:\\...`).

### 3. Tell the server where the data is

The server resolves the data folder from `SCRAPPER_DATA_DIR`, falling back to
`<working directory>/legislatie-descarcata`. Some clients let you set a working
directory, some do not. Setting `SCRAPPER_DATA_DIR` explicitly works
everywhere, so all examples below do that.

The server name is `legislatie`. Clients prefix the tool names with it:
`legislatie_search` in OpenCode and Kilo, `mcp__legislatie__search` in Claude
Code. All tools are read-only except `save_answer`, which only writes a new
Markdown file under `raspunsuri-ai/`, and `save_user_tax_context`, which only
writes `date-context/informatii-activitate.toml`; none of them modify the index
or the HTML files.

### 4. Connect your client

#### OpenCode

This repository already ships a working `opencode.json`, so when you open the
repository root in OpenCode the tools are available after `uv sync --extra ocr`. To set it
up by hand, or to use it from another project:

```jsonc
// opencode.json
{
  "$schema": "https://opencode.ai/config.json",
  "instructions": ["legislatie-descarcata/4-processed/AGENTS.md"],
  "mcp": {
    "servers": {
      "legislatie": {
        "type": "local",
        "command": [
          "/abs/path/scrapper-legislatie-just-cli/.venv/bin/python",
          "-m",
          "scrapper_legislatie_just.mcp_server"
        ],
        "cwd": "/abs/path/scrapper-legislatie-just-cli",
        "environment": {
          "SCRAPPER_DATA_DIR": "/abs/path/scrapper-legislatie-just-cli/legislatie-descarcata"
        }
      }
    }
  }
}
```

Or from the terminal:

```bash
opencode mcp add legislatie -- \
  /abs/path/scrapper-legislatie-just-cli/.venv/bin/python \
  -m scrapper_legislatie_just.mcp_server
opencode mcp list
```

Add `--env SCRAPPER_DATA_DIR=/abs/path/scrapper-legislatie-just-cli/legislatie-descarcata`
before the `--` when the data folder is not the default one. Add `--global` to
make the server available in every project.

#### Kilo Code

Kilo reads `kilo.jsonc` at the project root (or `~/.config/kilo/kilo.jsonc`
globally). The shape differs from other clients: `command` is one array and the
environment key is `environment`.

```jsonc
// kilo.jsonc
{
  "mcp": {
    "legislatie": {
      "type": "local",
      "command": [
        "/abs/path/scrapper-legislatie-just-cli/.venv/bin/python",
        "-m",
        "scrapper_legislatie_just.mcp_server"
      ],
      "environment": {
        "SCRAPPER_DATA_DIR": "/abs/path/scrapper-legislatie-just-cli/legislatie-descarcata",
        "AGENTS_INSTRUCTIONS_FILE": "./legislatie-descarcata/4-processed/AGENTS.md"
      },
      "enabled": true,
      "timeout": 30000
    }
  }
}
```

You can also use **Settings → MCP Servers → Add Server → Local (stdio)** and
paste the same command. After saving, check the server dot in that panel, or
ask Kilo to list the indexed acts.

#### Claude Code

Add it at user scope with the CLI:

```bash
claude mcp add --scope user --transport stdio \
  --env SCRAPPER_DATA_DIR=/abs/path/scrapper-legislatie-just-cli/legislatie-descarcata \
  legislatie -- /abs/path/scrapper-legislatie-just-cli/.venv/bin/python \
  -m scrapper_legislatie_just.mcp_server

claude mcp list
```

Or commit a project-scoped `.mcp.json` in the repository root:

```json
{
  "mcpServers": {
    "legislatie": {
      "type": "stdio",
      "command": "/abs/path/scrapper-legislatie-just-cli/.venv/bin/python",
      "args": ["-m", "scrapper_legislatie_just.mcp_server"],
      "env": {
        "SCRAPPER_DATA_DIR": "/abs/path/scrapper-legislatie-just-cli/legislatie-descarcata"
      }
    }
  }
}
```

Claude Code asks for approval the first time it sees a project-scoped server.
Tools appear as `mcp__legislatie__search`, `mcp__legislatie__context`, and so
on.

#### Codex CLI

Edit `~/.codex/config.toml`, or a project-scoped `.codex/config.toml` in a
trusted project:

```toml
[mcp_servers.legislatie]
command = "/abs/path/scrapper-legislatie-just-cli/.venv/bin/python"
args = ["-m", "scrapper_legislatie_just.mcp_server"]
cwd = "/abs/path/scrapper-legislatie-just-cli"
env = { SCRAPPER_DATA_DIR = "/abs/path/scrapper-legislatie-just-cli/legislatie-descarcata" }
startup_timeout_sec = 30
```

Or from the terminal:

```bash
codex mcp add legislatie \
  --env SCRAPPER_DATA_DIR=/abs/path/scrapper-legislatie-just-cli/legislatie-descarcata \
  -- /abs/path/scrapper-legislatie-just-cli/.venv/bin/python \
  -m scrapper_legislatie_just.mcp_server

codex mcp list
```

#### GitHub Copilot in VS Code

Create `.vscode/mcp.json` in the repo root. VS Code uses a `servers` key:

```json
{
  "servers": {
    "legislatie": {
      "type": "stdio",
      "command": "/abs/path/scrapper-legislatie-just-cli/.venv/bin/python",
      "args": ["-m", "scrapper_legislatie_just.mcp_server"],
      "cwd": "/abs/path/scrapper-legislatie-just-cli",
      "env": {
        "SCRAPPER_DATA_DIR": "/abs/path/scrapper-legislatie-just-cli/legislatie-descarcata"
      }
    }
  }
}
```

VS Code shows a **Start** button above the server entry; click it once. Use the
**Agent** mode in Copilot Chat afterwards. The Copilot CLI and the cloud agent
read the portable format instead, in `.mcp.json` (top-level `mcpServers`, no
`cwd`); reuse the Claude Code block above.

#### Cursor, Windsurf, Gemini CLI, Zed, Claude Desktop

Most clients read the portable `mcpServers` block shown for Claude Code. Only
the file location changes:

| Client | File |
|---|---|
| Cursor | `~/.cursor/mcp.json` or `.cursor/mcp.json` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` |
| Gemini CLI | `~/.gemini/settings.json` |
| Claude Desktop | `claude_desktop_config.json` |
| Zed | `.zed/settings.json`, under `context_servers` |

Zed uses a string command and an args array, without any wrapper keys:

```json
{
  "context_servers": {
    "legislatie": {
      "command": "/abs/path/scrapper-legislatie-just-cli/.venv/bin/python",
      "args": ["-m", "scrapper_legislatie_just.mcp_server"],
      "env": {
        "SCRAPPER_DATA_DIR": "/abs/path/scrapper-legislatie-just-cli/legislatie-descarcata"
      },
      "source": "custom"
    }
  }
}
```

### 5. Check the connection

The quickest check does not need an editor. Send a handshake and a tool call to
the server over stdin:

```bash
cd /abs/path/scrapper-legislatie-just-cli
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | .venv/bin/python -m scrapper_legislatie_just.mcp_server
```

You should get two JSON-RPC responses, the second listing `search`, `article`,
`show`, `neighbors`, `context`, `context_files`, `read_context`,
`convert_currency`, `save_answer`, `get_user_tax_context`,
`save_user_tax_context`, `docs`, and `cypher`. With the one-line
install, use the installed command on the last line instead:
`| scrapper-legislatie-just-mcp`. On Windows replace it with
`.venv\Scripts\python.exe -m scrapper_legislatie_just.mcp_server`, or
`scrapper-legislatie-just-mcp.exe`.

Then check the client view:

| Client | Check |
|---|---|
| OpenCode | `opencode mcp list`, or `/mcps` |
| Kilo Code | **Settings → MCP Servers**, green dot |
| Claude Code | `claude mcp list` |
| Codex | `codex mcp list`, or `/mcp` |
| VS Code Copilot | tools icon in Copilot Chat |
| Cursor / Windsurf / Zed | MCP panel in settings |

### 6. Ask a question

Open the repository and ask in Romanian. Mention the act name if you know it,
for example: `In Codul muncii, cate zile de concediu de odihna am?`

The assistant calls `legislatie_context` or `legislatie_search`, reads the
article with `legislatie_article`, follows citations with
`legislatie_neighbors`, and answers with `file://` links to the exact anchor in
`3-expanded/`. It then calls `legislatie_save_answer`, which writes the answer
to `raspunsuri-ai/<question-slug>.md` (ASCII slug, no diacritics).

### Give the assistant the answering rules

The rules are in `legislatie-descarcata/4-processed/AGENTS.md`. OpenCode is
already pointed at that file by `opencode.json`. Other clients look for their
own file name in the project root:

| Client | File it reads |
|---|---|
| Codex, Kilo, Cursor | `AGENTS.md` |
| Claude Code | `CLAUDE.md` |
| GitHub Copilot | `.github/copilot-instructions.md` |
| OpenCode | `instructions` array in `opencode.json` |

If your client expects a root file, add a small pointer instead of copying the
generated rules, so they stay in sync after every `process`:

```markdown
Read and follow `legislatie-descarcata/4-processed/AGENTS.md` before answering
legal questions, and use the `legislatie_*` MCP tools.
```

If you do not use MCP, the assistant can still run the CLI commands from this
README, but it will not automatically get the index tools described in
`AGENTS.md`.

### If a tool is missing

OpenCode starts the MCP server once, when the session starts. If you upgrade the
package (or edit the server code) while a session is open, the assistant keeps
the old tool list. A missing `convert_currency` or `get_user_tax_context` is a
sign of a stale server, and without it the model may fall back to `curl` for BNR
rates. Close and reopen OpenCode, then check `/mcps` again. The rules in
`AGENTS.md` tell the assistant to use `legislatie_convert_currency` first and to
fall back to a shell or web request only when the MCP tool is missing or
failing, saying so explicitly in the answer.

## Two ways to use the MCP server

This repository supports **two independent modes** with the same legal index:

| | Local (stdio) | Hosted (FastAPI / Streamable HTTP) |
|---|---|---|
| Who runs it | You, from this checkout | A server you deploy with Docker |
| Package | root `scrapper-legislatie-just-cli` | `server/` → `scrapper-legislatie-just-server` (depends on the CLI package via `git+https`, extra `[server]`) |
| Client setup | OpenCode starts `mcp_server.py` | OpenCode connects to a URL + token |
| Tools | All 13 tools | 8 read-only tools (see below) |
| Personal files | `date-context/`, `raspunsuri-ai/` on your PC | **None** — no user data on the server |
| Result links | `file:///.../3-expanded/...#anchor` | `https://mcp.chatcodfiscal.ro/laws/3-expanded/...#anchor` |
| Instructions file | `AGENTS.md` | `AGENTS-cloud.md` |

The local mode is documented above and works exactly as before: `file://`
links, the fiscal-context questionnaire, PDF reading and `save_answer` all stay
on your machine. Nothing in this section changes it.

Both packages share one codebase — the server imports `LegalIndex`, the
citation graph, the link builders and the Markdown formatters from
`scrapper_legislatie_just`. No index or query logic is duplicated.

## Hosted (cloud) MCP with FastAPI

### Packages and installation

The hosted server is an independent package in `server/`. Its only runtime
dependency is the CLI package with the `[server]` extra, installed straight
from GitHub:

```toml
# server/pyproject.toml
dependencies = [
  "scrapper-legislatie-just-cli[server] @ git+https://github.com/ClimenteA/scrapper-legislatie-just-cli.git@main",
]
```

That means you can install the server **without this monorepo**:

```bash
uv tool install "git+https://github.com/ClimenteA/scrapper-legislatie-just-cli.git@main#subdirectory=server"
# provides: scrapper-legislatie-just-web, scrapper-legislatie-just-indexer
```

`[server]` adds FastAPI, the MCP SDK, JWT and the scheduler to the lean local
install. The `ocr` extra stays on the CLI package, so the indexer image can
install `[ocr]` while the web image skips the heavy torch/CUDA wheels.

When developing from a checkout, use the root `uv sync` described above: the
workspace source override replaces the GitHub URL with the local package. The
git URL is used only when the server package is installed on its own. The
`server/` Dockerfile is still built with the repo root as context (see
[Run with Docker](#2-run-with-docker)) so it sees the workspace; the git URL
matters for standalone `uv tool install`. Pin `@main` to a tag such as
`@v0.2.0` for reproducible standalone installs.


The hosted server exposes only **stateless, read-only tools**: `search`,
`article`, `show`, `neighbors`, `context`, `docs`, `cypher` and
`convert_currency` (BNR rates). Personal-file tools (`context_files`,
`read_context`, `get_user_tax_context`, `save_user_tax_context`, `save_answer`)
are deliberately **not** available here. The server never creates
`date-context/` or `raspunsuri-ai/` and stores no documents, answers or fiscal
profiles. Because the web app never reads PDFs, its image installs only the
base dependencies (no optional `ocr` extra) and stays around **220 MB**. The
separate indexer image **does** install the `ocr` extra, because the monthly
reindex runs OCR on base64 images embedded in the downloaded HTML.

Links returned by the hosted tools are HTTPS URLs. Caddy serves only the
`3-expanded/` folder read-only at `/laws/*`, so `law.db` and `graph.db` are
never publicly downloadable. The generated `AGENTS-cloud.md` contains the same
answering rules as `AGENTS.md`, minus the tool calls that do not exist in this
mode.

### 1. Configuration

All deployment files live in `server/`: `docker-compose.yml`, `Dockerfile`,
`Caddyfile`, `.env.example` and your `.env`. The data folder stays at the
repository root, one level above `server/`.

From `server/`, copy `.env.example` to `.env` and set at least these two values:

```bash
cd server
cp .env.example .env
```

```bash
ADMIN_SECRET_KEY=the-admin-secret-key-change-it
TOKEN_SIGNING_SECRET=<long-random-value>   # python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`ADMIN_SECRET_KEY` is a plain string: the `admin_secret_key` you send when
issuing a token must be exactly that value. `TOKEN_SIGNING_SECRET` signs the
issued JWTs; **rotating it revokes every token at once**. Neither value is
written to disk by the app — they are read from the environment at request
time.

Other supported variables:

| Variable | Default | Purpose |
|---|---|---|
| `SCRAPPER_DATA_DIR` | `<cwd>/legislatie-descarcata` | Data root (`law.db`, `graph.db`, `3-expanded`) |
| `PUBLIC_LAWS_BASE_URL` | `https://mcp.chatcodfiscal.ro/laws` | Prefix for public result links |
| `MCP_ALLOWED_HOSTS` | `mcp.chatcodfiscal.ro`, `localhost`, `127.0.0.1` (+ `:port` variants) | MCP DNS-rebinding allowlist |
| `MCP_DISABLE_DNS_REBINDING` | `0` | Set `1` only when a trusted proxy owns the `Host` header |
| `WEB_WORKERS` | `2` | uvicorn worker processes |
| `WEB_HOST` / `WEB_PORT` | `0.0.0.0` / `8000` | Bind address |
| `REINDEX_ON_START` | `0` | `1` builds the index on the indexer's first start |
| `REINDEX_OCR` | `1` | OCR of base64 images during reindex (indexer only) |
| `TZ` | `Europe/Bucharest` | Container timezone |

### 2. Run with Docker

`server/docker-compose.yml` starts three services: `caddy-proxy`,
`chatcodfiscal-app` (the FastAPI + MCP app, built without the OCR extra) and
`chatcodfiscal-indexer` (one scheduler process, built with `INSTALL_OCR=1`).
Both images use `server/Dockerfile` with the repository root as build context
(`context: ..`, resolved from the compose file), so they see the shared CLI
code, the workspace and the frozen `uv.lock`.
`caddy-proxy` bind-mounts only `../legislatie-descarcata/3-expanded` (read-only)
at `/data/legislatie-descarcata/3-expanded`, so `law.db` and `graph.db` are never
exposed by the public web server. `chatcodfiscal-app` and
`chatcodfiscal-indexer` bind-mount the whole `../legislatie-descarcata` at
`/data/legislatie-descarcata`, so the index is visible both in the containers
and on the host.

```bash
cd server
cp .env.example .env   # already done in step 1
# edit .env, then build the index on the first launch:
REINDEX_ON_START=1 docker compose up -d --build
# afterwards set REINDEX_ON_START=0 and restart if needed
```

Run every `docker compose` command from `server/` so Compose finds its `.env`
and resolves the relative bind-mount paths.

The `Caddyfile` routes `mcp.chatcodfiscal.ro` as follows:

- `/laws/*` → read-only `3-expanded/` HTML;
- `/static/*` → optional static files;
- everything else (including `/mcp`, `/admin/*`, `/health`, `/docs`) → the
  FastAPI app on port 8000.

The OpenAPI UI is at `https://mcp.chatcodfiscal.ro/docs`; `/health` reports
whether `law.db` and `graph.db` exist.

### 3. Issue a token

Use the admin endpoint (also available in the OpenAPI docs):

```bash
curl -X POST https://mcp.chatcodfiscal.ro/admin/tokens \
  -H 'Content-Type: application/json' \
  -d '{
    "user_email": "user@example.com",
    "token_expire_date": "2027-01-01T23:59:59+02:00",
    "admin_secret_key": "the-admin-secret-key-change-it"
  }'
```

`token_expire_date` is ISO 8601. Times without an offset are interpreted as
**Europe/Bucharest** local time; `Z` and explicit offsets are respected; past
dates are rejected with `422`. The response contains the signed token — it is
returned once and is not stored anywhere. Expiry and signature are verified on
every request; invalid tokens get `401`.

### 4. Connect OpenCode

Give the user this configuration (they only need OpenCode — no repository or
Python install):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "instructions": ["AGENTS-cloud.md"],
  "mcp": {
    "servers": {
      "legislatie": {
        "type": "remote",
        "url": "https://mcp.chatcodfiscal.ro/mcp",
        "oauth": false,
        "codemode": false,
        "headers": { "Authorization": "Bearer {env:MCP_API_KEY}" }
      }
    }
  }
}
```

Set `MCP_API_KEY` to the token from step 3. `codemode: false` keeps the native
`legislatie_*` tool names used by `AGENTS-cloud.md`. Copy `AGENTS-cloud.md`
next to OpenCode (it is regenerated into `4-processed/` by `process`).

### 5. Reindexing

The indexer container runs APScheduler with a cron trigger for the **15th of
each month at 03:00 Europe/Bucharest**, using the same pipeline as
`run-all`. It runs in its own container so web workers never block, and a file
lock prevents overlapping runs. `law.db` and `graph.db` are written to temporary
files and published with `os.replace`, so readers always see a complete index.

For a manual rebuild, call (admin only):

```bash
curl -X POST https://mcp.chatcodfiscal.ro/admin/reindex \
  -H 'Content-Type: application/json' \
  -d '{"admin_secret_key": "the-admin-secret-key-change-it", "force": false}'
```

### 6. Local mode stays unchanged

To keep using the full local toolset, configure the stdio server as described
in [Using it with an AI assistant](#using-it-with-an-ai-assistant). It still
uses `AGENTS.md`, `file://` links, and local `date-context/` / `raspunsuri-ai/`
folders. Both modes can read the same index; only the hosted one imposes the
stateless, read-only subset.

## Persistent fiscal context

The assistant can personalise fiscal answers from a small TOML file:

```text
date-context/informatii-activitate.toml
```

It is optional. Before answering a new legal or fiscal question, the assistant
calls `legislatie_get_user_tax_context`. If the file is missing, the assistant
offers a short questionnaire once: legal form, activity, CAEN code, tax regime,
VAT status, employees, activity zone and optional details. The user can decline
and still get the general legal answer; the assistant shows a short warning and
continues.

When the user confirms, the assistant calls
`legislatie_save_user_tax_context` with only the confirmed fields. The tool
creates the folder and file, merges the values, keeps fields it does not know,
and verifies the written TOML. Values are stored as readable text, not as
internal codes.

Example file:

```toml
[activitate]
forma_juridica = "PFA/II/IF"
domeniu = "Servicii IT și dezvoltare software"
caen = "6202"
regim_impozitare = "Sistem real (PFA/II/IF)"
statut_tva = "Neplătitor de TVA (regim de scutire)"
are_angajati = false
zona_activitate = "Exclusiv în România"
```

`date-context/` is in `.gitignore`, so the context stays local. The file is
plain TOML: add fields freely or edit it by hand; unknown fields are preserved
when the assistant updates the file. Do not store CNP, personal address, IBAN
or other data that is not needed to personalise a fiscal answer.

With an MCP client the two operations are:

```text
get_user_tax_context()
save_user_tax_context({"statut_tva": "Plătitor de TVA (perioadă lunară)"})
```

To inspect exactly what the assistant sees:

```bash
uv run scrapper-legislatie-just-cli tax-context
```

If the file is invalid TOML, the read tool reports the parse error and the
assistant continues with the general legal answer instead of failing. The
existing file is never overwritten silently while it is invalid; fix or delete
it before updating.

## Where the files are

Everything is stored in `legislatie-descarcata/` in the directory where you run
the command:

```text
legislatie-descarcata/
├── 1-raw/        downloaded HTML
├── 2-clean/      cleaned HTML
├── 3-expanded/   HTML, Markdown, and TOC JSON per act
└── 4-processed/  search index, citation graph, AGENTS.md

date-context/     PDFs, images and screenshots supplied by the user
├── informatii-activitate.toml  optional persistent fiscal context
└── .converted/   cached Docling Markdown extractions
```

Final answers saved with `save-answer` (or the `save_answer` MCP tool) go to
`raspunsuri-ai/` next to the data folder, one Markdown file per question. Use
`--output-dir` or `$SCRAPPER_ANSWERS_DIR` to store them elsewhere. User files
read by `read-context` live in `date-context/`; use `--context-dir` or
`$SCRAPPER_CONTEXT_DIR` to store them elsewhere.

Keep `3-expanded/` and `4-processed/`: result links point to the HTML files in
`3-expanded/`, and the index lives in `4-processed/`.

To store the library somewhere else:

```bash
uv run scrapper-legislatie-just-cli run-all --data-dir /path/to/library
```

Every command accepts the same `--data-dir`.

## Uninstall

One command, mirroring the installer:

Linux and macOS (also Git Bash, MSYS2 and WSL on Windows):

```bash
curl -LsSf https://raw.githubusercontent.com/ClimenteA/scrapper-legislatie-just-cli/main/uninstall.sh | sh
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/ClimenteA/scrapper-legislatie-just-cli/main/uninstall.ps1 | iex
```

It removes the `uv tool` environment (the CLI and the MCP server) and the
`monolith` binary that the installer placed in `~/.local/bin`. Options:

| Option | Meaning |
|---|---|
| `--keep-monolith` | Leave the `monolith` binary in place |
| `--purge-data` | Also delete `./legislatie-descarcata` (or `$SCRAPPER_DATA_DIR`) |
| `--dry-run` | Print what would be removed, change nothing |

When piping, pass options after `sh -s --`:

```bash
curl -LsSf .../uninstall.sh | sh -s -- --dry-run
```

The same values work as environment variables: `SCRAPPER_KEEP_MONOLITH=1` and
`SCRAPPER_DATA_DIR`.

`uv` itself is kept, because other tools on the machine may use it; remove it
with `uv self uninstall`. `monolith` installed with Homebrew, snap, or winget
is also kept. The data folder is never deleted without `--purge-data`, so you
can reinstall later and keep the laws you already downloaded.

## Common problems

**`monolith` is missing**

The download step will not run until `monolith --version` works in the same
terminal. The installer tries to fetch the prebuilt binary automatically; if
that did not work, install it by hand:

| Platform | Command |
|---|---|
| macOS | `brew install monolith` |
| Ubuntu / Debian | `curl -fLsS https://github.com/Y2Z/monolith/releases/latest/download/monolith-cli-linux-x86_64 -o ~/.local/bin/monolith && chmod +x ~/.local/bin/monolith` |
| Windows | `winget install --id=Y2Z.Monolith -e` |
| Any | prebuilt binaries on the [releases page](https://github.com/Y2Z/monolith/releases) |

**Downloads fail or stop**

`legislatie.just.ro` has bot protection and rate limits. Wait a few minutes and
try again. For a first test, always download a single act:

```bash
uv run scrapper-legislatie-just-cli download --slug legea-contabilitatii --limit 1
```

**Search returns nothing**

Make sure the index was built:

```bash
uv run scrapper-legislatie-just-cli process
```

Then try fewer words, a synonym, or the act name with `--doc`.

**The law text is old**

Run `run-all` again. By default it downloads the latest consolidation. To keep
older versions too, add `--all-versions` to `download`.

**The MCP server does not start in my editor**

Editors start the process without your shell `PATH`. Use the absolute path to
`.venv/bin/python` (`.venv\Scripts\python.exe` on Windows), not `python`, `uv`,
or `uvx`. Run the stdin check from
[Check the connection](#5-check-the-connection) to see the real error: a
traceback means the interpreter or the package is wrong, while clean JSON-RPC
output means the problem is in the client configuration file.

**The tools exist but every answer is an error about the index**

The server cannot find `4-processed/law.db`. It looks in `SCRAPPER_DATA_DIR`
first, then in `<working directory>/legislatie-descarcata`. Set
`SCRAPPER_DATA_DIR` to the absolute data folder, or set the server working
directory to the repository root, then reload the client. Build the index with
`process` if `law.db` does not exist yet.

**The assistant answers from memory instead of using the tools**

The client did not load the answering rules. Point it at
`legislatie-descarcata/4-processed/AGENTS.md` as described in
[Give the assistant the answering rules](#give-the-assistant-the-answering-rules).

## Advanced

### Add your own laws

The packaged source list is
`src/scrapper_legislatie_just/sources.toml`. You can add a `sources.toml` in the
directory where you run the CLI; it is picked up automatically.

```toml
[[sources]]
numar_link = 1576
nume = "Legea contabilitatii nr. 82/1991"
slug = "legea-contabilitatii"
descriere = "Stabileste principiile de baza ale contabilitatii."
```

`numar_link` is the number at the end of
`https://legislatie.just.ro/Public/DetaliiDocument/<numar_link>`.

You can also point to a specific file:

```bash
export SCRAPPER_SOURCES_FILE=/path/to/sources.toml
```

Removing a source from `sources.toml` is enough: the next `download` (or
`run-all`) deletes its files from `1-raw`, `2-clean` and `3-expanded`. If the
processed index existed, it is invalidated so the next `process` rebuilds it
without the removed act.

### Environment variables

| Variable | Purpose |
|---|---|
| `SCRAPPER_DATA_DIR` | Default data folder |
| `SCRAPPER_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING` |
| `SCRAPPER_LOG_FILE` | Optional log file |

### The citation graph and Cypher

`process` writes two databases: `law.db` holds the text and the search index,
and `graph.db` holds the **citation graph** of the same corpus. The graph is
made of nodes (one per article, paragraph, letter, note, and so on) and edges
(one per citation or mention).

**Cypher** is the query language for graph databases, created for Neo4j. It is
declarative: instead of loops you draw the shape you are looking for with ASCII
art, and the engine returns every match.

```cypher
(a:Articol)-[r:CITEAZA_ARTICOL]->(b:Articol)
```

reads as "an article `a` that cites, with edge `r`, an article `b`". Direction
matters: `(a)-[r]->(b)` follows the arrow from `a` to `b`, so with `CITEAZA` it
means "a cites b", while `(a)<-[r]-(b)` means "b cites a".

No Neo4j server is involved. The graph is stored in SQLite and queried with
[GraphQLite](https://github.com/colliery-io/graphqlite), which implements
Cypher on top of SQLite. `uv sync` installs it, so the command works out of the
box.

Nodes carry these labels: `Act`, `Titlu`, `Capitol`, `Sectiune`, `Articol`,
`Alineat`, `Punct`, `Litera`, `Paragraf`, `Linie`, `Nota`, `Anexa`. Every node
has the properties `id`, `label`, `kind`, `doc`, `doc_title`, `version`,
`anchor`, `number`, `article_id`, `html`, and `link` (the clickable `file://`
URL).

Edges come in four types. `CITEAZA` and `MENTIONEAZA` connect the exact nodes
where the reference appears. `CITEAZA_ARTICOL` and `MENTIONEAZA_ARTICOL`
connect whole articles and carry a `count` of how many references were rolled
up, which is what most questions need, because citations usually start in a
letter or a paragraph, not in the article itself.

Run a query with the CLI:

```bash
uv run scrapper-legislatie-just-cli cypher \
  "MATCH (a:Articol)-[r:CITEAZA_ARTICOL]->(b:Articol {doc: 'codul-fiscal', number: '60'})
   RETURN a.number AS articol, r.count AS citari
   ORDER BY citari DESC"
```

or through the `legislatie_cypher` MCP tool with the same string.

Useful starting queries. Change the `doc` and `number` values to your own act.

The articles that cite Codul fiscal art. 60:

```cypher
MATCH (a:Articol)-[r:CITEAZA_ARTICOL]->(b:Articol {doc: 'codul-fiscal', number: '60'})
RETURN a.number AS articol, r.count AS citari
ORDER BY citari DESC
```

The most cited articles in the whole corpus:

```cypher
MATCH (a:Articol)-[r:CITEAZA_ARTICOL]->(b:Articol)
RETURN b.doc AS act, b.number AS articol, count(r) AS citari
ORDER BY citari DESC
LIMIT 10
```

How many articles each indexed act has:

```cypher
MATCH (a:Articol)
RETURN a.doc AS act, count(*) AS articole
ORDER BY articole DESC
```

The exact HTML link of one article:

```cypher
MATCH (a:Articol {doc: 'codul-muncii', number: '145'})
RETURN a.label AS articol, a.link AS link
```

Three limits worth knowing. The graph stores structure, not text, so do not try
to full-text search it (`a.label` is just `Articolul 60`); use `search` or
`context` for wording, and `cypher` for relationships. GraphQLite does not
understand `--` or `/* */` comments, so paste queries without comments. And for
one or two hops around a known article, `neighbors` and `show` are simpler than
writing Cypher.

### MCP server

The stdio server is a single module with no third-party MCP dependency, so it
runs in the same virtualenv as the CLI. The installed command is
`scrapper-legislatie-just-mcp`; from a source checkout the equivalent is:

```bash
uv run python -m scrapper_legislatie_just.mcp_server
```

It speaks JSON-RPC 2.0 over stdin/stdout and exposes thirteen tools:

| Tool | Purpose |
|---|---|
| `search` | BM25 search over the whole corpus, with `file://` links |
| `article` | One article with all paragraphs, letters, and notes |
| `show` | One node (article, paragraph, letter, note) with parents and citations |
| `neighbors` | Incoming and outgoing citations; `subtree=true` covers the article |
| `context` | Ready-to-read evidence pack for a question |
| `context_files` | Lists user PDFs/images in `date-context/` |
| `read_context` | Extracts text from a user PDF/image with Docling OCR |
| `convert_currency` | Converts a foreign amount to RON with the BNR rate and effective date |
| `get_user_tax_context` | Reads the optional fiscal context from `date-context/informatii-activitate.toml` |
| `save_user_tax_context` | Creates or updates that TOML file, preserving fields not being changed |
| `save_answer` | Writes the final answer to `raspunsuri-ai/<question-slug>.md` |
| `docs` | Indexed acts and their consolidation dates |
| `cypher` | Cypher query over `4-processed/graph.db`; see [The citation graph and Cypher](#the-citation-graph-and-cypher) |

All tools are read-only except `save_answer` and `save_user_tax_context`.

See [Using it with an AI assistant](#using-it-with-an-ai-assistant) for the
per-client configuration, and [Common problems](#common-problems) when a client
cannot start it.

### Tests

The test suite does not use the network. From the checkout, `uv run pytest`
covers both packages (the root `uv sync` installs the server package too):

```bash
uv run pytest
```
