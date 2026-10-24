# Legislatie Just CLI

Download Romanian laws from `legislatie.just.ro`, keep a local copy, and search
it offline. No account, API key, or cloud service is required. The search index
is built on your computer.

## Quick start

### 1. Install

You need:

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- [`monolith`](https://github.com/Y2Z/monolith/releases) available on `PATH`

Install the program:

```bash
cd scrapper-legislatie-just-cli
uv sync
```

Check that it works:

```bash
monolith --version
uv run scrapper-legislatie-just-cli --help
```

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
| Show data locations | `uv run scrapper-legislatie-just-cli paths` |

Use `--help` after any command to see its options.

## What each step does

| Step | Command | What it does |
|---|---|---|
| 1 | `download` | Downloads the latest printable HTML from `legislatie.just.ro` |
| 2 | `clean` | Cleans the HTML and makes it readable |
| 3 | `expand` | Creates one folder per act with HTML, Markdown, and a table of contents |
| 4 | `process` | Builds the local search index and citation graph |

`run-all` runs all four steps in order.

## Using it with an AI assistant

After `process`, the folder `legislatie-descarcata/4-processed/` contains the
index and a generated `AGENTS.md` with instructions for the assistant.

The generated instructions expect the MCP tools (`legislatie_search`,
`legislatie_context`, and so on). Connect the MCP server first:

```bash
uv run python -m scrapper_legislatie_just.mcp_server
```

Then open `legislatie-descarcata/4-processed/` in Kilo or OpenCode and ask legal
questions in Romanian. Mention the act name if you know it, for example:
`In Codul muncii, cate zile de concediu de odihna am?`

The assistant is instructed to search the index, cite the exact HTML link, and
check related articles instead of answering from memory.

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

## Common problems

**`monolith` is missing**

The download step will not run. Install `monolith` from its releases page and
make sure `monolith --version` works in the same terminal.

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

### MCP server

The stdio MCP server exposes the same search, article, and citation tools:

```bash
uv run python -m scrapper_legislatie_just.mcp_server
```

Configure this command in your MCP client if you want the `legislatie_*` tools.

### Tests

The test suite does not use the network:

```bash
uv run pytest
```
