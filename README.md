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

The OCR stack (`docling`, `easyocr`) and the Cypher graph (`graphqlite`) are
installed by default, because `download`, `expand` and `cypher` need them. The
first install downloads a few hundred MB and takes several minutes; later runs
are cached.

#### From source (development)

```bash
git clone https://github.com/ClimenteA/scrapper-legislatie-just-cli.git
cd scrapper-legislatie-just-cli
uv sync
```

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
| Show data locations | `uv run scrapper-legislatie-just-cli paths` |

Use `--help` after any command to see its options.

## What each step does

| Step | Command | What it does |
|---|---|---|
| 1 | `download` | Downloads the latest printable HTML from `legislatie.just.ro` |
| 2 | `clean` | Cleans the HTML and makes it readable |
| 3 | `expand` | Creates one folder per act with HTML, Markdown, and a table of contents |
| 4 | `process` | Builds `law.db` (search index) and `graph.db` (citation graph, queried with Cypher) |

`run-all` runs all four steps in order.

## Using it with an AI assistant

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
Code. All seven tools are read-only: they never modify the index or the HTML
files.

### 4. Connect your client

#### OpenCode

This repository already ships a working `opencode.json`, so when you open the
repository root in OpenCode the tools are available after `uv sync`. To set it
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
        "SCRAPPER_DATA_DIR": "/abs/path/scrapper-legislatie-just-cli/legislatie-descarcata"
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
`show`, `neighbors`, `context`, `docs`, and `cypher`. With the one-line
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
`3-expanded/`.

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

## Where the files are

Everything is stored in `legislatie-descarcata/` in the directory where you run
the command:

```text
legislatie-descarcata/
├── 1-raw/        downloaded HTML
├── 2-clean/      cleaned HTML
├── 3-expanded/   HTML, Markdown, and TOC JSON per act
└── 4-processed/  search index, citation graph, AGENTS.md
```

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

It speaks JSON-RPC 2.0 over stdin/stdout and exposes seven read-only tools:

| Tool | Purpose |
|---|---|
| `search` | BM25 search over the whole corpus, with `file://` links |
| `article` | One article with all paragraphs, letters, and notes |
| `show` | One node (article, paragraph, letter, note) with parents and citations |
| `neighbors` | Incoming and outgoing citations; `subtree=true` covers the article |
| `context` | Ready-to-read evidence pack for a question |
| `docs` | Indexed acts and their consolidation dates |
| `cypher` | Cypher query over `4-processed/graph.db`; see [The citation graph and Cypher](#the-citation-graph-and-cypher) |

See [Using it with an AI assistant](#using-it-with-an-ai-assistant) for the
per-client configuration, and [Common problems](#common-problems) when a client
cannot start it.

### Tests

The test suite does not use the network:

```bash
uv run pytest
```
