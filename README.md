# scrapper-legislatie-just-cli

A small standalone Python CLI that downloads and processes legislatie.just.ro pages.

| Step | Command    | What it does                                                        |
| ---- | ---------- | ------------------------------------------------------------------- |
| 1    | `download` | Downloads the latest printable HTML consolidation per source           |
| 2    | `clean`    | Normalizes the raw HTML and writes semantic, readable HTML           |
| 3    | `expand`   | Groups files by source, builds a TOC JSON and converts HTML to `.md` |


## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- **Required:** [`monolith`](https://github.com/Y2Z/monolith) on `PATH`.
  The download step refuses to run without it.

Docling is installed as a normal dependency; it is used by `expand` for the
HTML → Markdown conversion and by `clean` for OCR of base64 images.

## Install

```bash
cd scrapper-legislatie-just-cli
uv sync
```

This creates `.venv/` and installs all dependencies, including `typer`,
`requests`, `beautifulsoup4`, `docling` and `loguru`.

Verify the installation:

```bash
monolith --version
uv run scrapper-legislatie-just-cli --help
uv run scrapper-legislatie-just-cli list-sources
```

## Sources configuration

The source list is no longer hard-coded in Python. It lives in
[`src/scrapper_legislatie_just/sources.toml`](src/scrapper_legislatie_just/sources.toml):

```toml
[[sources]]
numar_link = 1576
nume = "Legea contabilitatii nr. 82/1991"
slug = "legea-contabilitatii"
descriere = """
Stabileste principiile de baza ale contabilitatii...
"""
```

The document URL is derived automatically:

```text
https://legislatie.just.ro/Public/DetaliiDocument/<numar_link>
```

TOML was chosen over YAML because:

- Python 3.11+ can read it with the standard-library `tomllib` (no extra
  dependency),
- it supports comments and clean multi-line strings for the descriptions,
- it is not indentation-sensitive, which makes large source lists less
  error-prone to edit.

If a `sources.toml` exists in the directory where you run the CLI, it is used
automatically instead of the packaged file. This is the easiest way to work
with your own source list:

```text
my-project/
├── sources.toml          # picked up automatically
└── ...
```

The resolution order is:

1. explicit path passed to `load_sources()`
2. `SCRAPPER_SOURCES_FILE`
3. `./sources.toml` in the current working directory
4. the `sources.toml` packaged with the CLI

A custom file can also be selected explicitly with:

```bash
export SCRAPPER_SOURCES_FILE=/path/to/sources.toml
```

## Data layout

By default everything is stored under `legislatie-fiscala/` in the directory
where you run the CLI. Each folder is prefixed with the step that writes it:

```text
<current working directory>/legislatie-fiscala/
├── 1-raw/       # step 1: downloaded printable HTML
├── 2-clean/     # step 2: cleaned semantic HTML
└── 3-expanded/  # step 3: <slug>/ folders with HTML, MD and toc-<slug>.json
```

Use `--data-dir /some/path` on any command to use another root, or set:

```bash
export SCRAPPER_DATA_DIR=/some/path
```

Show the resolved paths:

```bash
uv run scrapper-legislatie-just-cli paths
```

## Quick start

Download only one document from one source (useful while testing, because
legislatie.just.ro has bot protection and rate limits):

```bash
uv run scrapper-legislatie-just-cli download \
  --slug legea-contabilitatii \
  --limit 1
```

Clean the downloaded file (skip OCR if you do not need images):

```bash
uv run scrapper-legislatie-just-cli clean \
  --slug legea-contabilitatii \
  --limit 1 \
  --no-ocr
```

Expand to Markdown and build the TOC:

```bash
uv run scrapper-legislatie-just-cli expand --slug legea-contabilitatii
```

Or run steps 1-3 in one go:

```bash
uv run scrapper-legislatie-just-cli run-all \
  --slug legea-contabilitatii \
  --limit 1 \
  --no-ocr
```

## Commands

### `download` (step 1)

```bash
uv run scrapper-legislatie-just-cli download [OPTIONS]
```

| Option | Description |
| ------ | ----------- |
| `-s, --slug TEXT` | Source slug. Repeat the option for several sources. Default: all sources. |
| `-n, --limit INT` | Maximum number of documents to download. |
| `-f, --force` | Download even if a file with the same document version exists. |
| `--all-versions` | Download every consolidation from the last 5 years instead of only the latest. |
| `--dry-run` | Discover URLs and print them without writing files. |
| `--data-dir PATH` | Data root. |
| `-v, --verbose` | Debug logging. |

The downloader looks at the consolidation history and keeps only the newest
version of each document. It then finds the "Forma printabilă" link and saves
files as:

```text
<numar_link>-<slug>-<YYYY-MM-DD>.html
```

Pass `--all-versions` to download every consolidation from the last 5 years.
A local file for an older consolidation does not block downloading a newer one.

### `clean` (step 2)

```bash
uv run scrapper-legislatie-just-cli clean [OPTIONS]
```

| Option | Description |
| ------ | ----------- |
| `-s, --slug TEXT` | Source slug (repeatable). |
| `-n, --limit INT` | Maximum number of files to clean. |
| `-f, --force` | Re-clean files that already exist in `2-clean/`. |
| `--ocr / --no-ocr` | Run OCR on base64 images. Default: `--ocr`. |
| `--data-dir PATH` | Data root. |
| `-v, --verbose` | Debug logging. |

The cleaner removes abrogated content, unused attributes and empty markup,
injects CSS and a table of contents, resolves internal article references,
converts the structure to semantic HTML and adds a source header.

### `expand` (step 3)

```bash
uv run scrapper-legislatie-just-cli expand [OPTIONS]
```

| Option | Description |
| ------ | ----------- |
| `-s, --slug TEXT` | Source slug (repeatable). |
| `-n, --limit INT` | Maximum number of HTML → Markdown conversions. |
| `-f, --force` | Re-copy and re-convert existing files. |
| `--data-dir PATH` | Data root. |
| `-v, --verbose` | Debug logging. |

Output per source:

```text
legislatie-fiscala/3-expanded/<slug>/
├── <number>-<slug>-<date>.html
├── <number>-<slug>-<date>.md
└── toc-<slug>.json
```

### `run-all`

Runs `download`, `clean` and `expand` in that order. It accepts the same
`--slug`, `--limit`, `--force`, `--all-versions`, `--data-dir`, `--verbose`
and `--ocr/--no-ocr` options. With `--dry-run`, only the download discovery
runs.

### `list-sources` / `paths`

```bash
uv run scrapper-legislatie-just-cli list-sources   # show slugs and document numbers
uv run scrapper-legislatie-just-cli paths          # show data directories
```

## Environment variables

| Variable | Description |
| -------- | ----------- |
| `SCRAPPER_DATA_DIR` | Default data root (overridden by `--data-dir`). When unset, `<current working directory>/legislatie-fiscala` is used. |
| `SCRAPPER_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, ... Default: `INFO`. |
| `SCRAPPER_LOG_FILE` | Optional log file path. Rotates at 100 MB. |

## Bot protection / rate limits

`legislatie.just.ro` uses bot protection and may return challenge pages or
rate-limit requests. The downloader already:

- rotates browser headers and user agents during discovery,
- adds jitter between requests,
- retries with exponential backoff,
- uses `monolith` with a randomized user agent for the actual download,
- stops immediately with a clear error if `monolith` is not installed.

For experiments, always prefer a small sample:

```bash
uv run scrapper-legislatie-just-cli download --slug legea-contabilitatii --limit 1
```

## Tests

The test suite uses two tiny local HTML fixtures and does **not** touch the
network. It exercises steps 2-3 end to end, including Docling HTML → Markdown
conversion:

```bash
uv run pytest
```

Expected result: all tests pass.

## Differences from the Django project

- Django settings were replaced by `scrapper_legislatie_just/config.py`.
- `webapp.logger` was replaced by a local Loguru configuration.
- `utils.browser_headers`, `jitter_call` and the base64 OCR helper were
  copied into this package.
- The hard-coded source list was moved from `surse.py` to `sources.toml` and
  is parsed with `tomllib`.
- `monolith` is mandatory; there is no plain-`requests` download fallback.
- The cleaner and expander accept `--slug`, `--limit` and `--force` so they
  can process small samples.
