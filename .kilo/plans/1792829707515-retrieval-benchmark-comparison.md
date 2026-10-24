# Retrieval benchmark: 3-expanded Markdown vs 4-processed index

## Goal

Add an offline, deterministic pytest benchmark that compares two ways of finding
the legal evidence needed to answer a question:

- **Method A — old/frontend path:** read `3-expanded/*/toc-*.json` to choose the
  relevant act, then search the chosen `3-expanded/<slug>/*.md` file.
- **Method B — indexed path:** query `4-processed/law.db` (FTS5/BM25) and build
  context with the citation graph in `4-processed/graph.db`.

The benchmark measures **retrieval accuracy** and **latency**, not LLM answer
prose quality. It is reproducible, needs no network, and skips automatically
when the real corpus/index is absent.

## Decisions already made

- Deterministic retrieval benchmark, no LLM judge.
- Old method is an in-process lexical baseline, not subprocess `rg`/`cat`.
- Gold set grows from 9 to ~30 hand-curated, HTML-verified cases.
- Evaluate two modes: supplied `doc` and blind document selection.
- Gate accuracy only: indexed `any_hit@5` must not be below the Markdown
  baseline. Latency is reported, never asserted.
- Reusable logic lives in `src/scrapper_legislatie_just/benchmark.py`; pytest
  writes `tests/artifacts/retrieval_benchmark.{json,md}` and prints a summary.

## Files to change

1. `tests/fixtures/real_questions.jsonl` — expand to ~30 cases.
2. `src/scrapper_legislatie_just/benchmark.py` — new benchmark library.
3. `src/scrapper_legislatie_just/query.py` — add optional `doc` filter to
   `LegalIndex.context`.
4. `src/scrapper_legislatie_just/mcp_server.py` — expose `doc` on the `context`
   tool.
5. `src/scrapper_legislatie_just/cli.py` — add `--doc` to the `context` command.
6. `tests/test_retrieval_benchmark.py` — new tests.
7. `tests/test_real_corpus.py` — tolerate the new optional gold fields (it
   already does, but verify).
8. `.gitignore` — add `tests/artifacts/`.

## Gold dataset

Extend `tests/fixtures/real_questions.jsonl` from 9 to about 30 cases. Keep the
existing schema backward-compatible:

```json
{"question": "scutire impozit venit", "doc": "codul-fiscal", "articles": ["60"], "notes": "..."}
{"question": "CAEN 6210", "doc": "caen-rev-3", "anchors": ["v-1708"], "expected_terms": ["6210", "softului"], "notes": "..."}
```

Rules:

- Questions are written in Romanian and read naturally, not copied from index
  tokens.
- Cover at least 15 of the 39 indexed acts. Keep the existing 9 and add new
  cases for acts such as `cod-procedura-fiscala`, `legea-contabilitatii`,
  `legea-societatilor`, `lege-pensii`, `oug-concedii-medicale`,
  `norme-metodologice-de-aplicare-a-codului-fiscal`, `lege-somaj`, `lege-sanatate`.
- Every article-based case must have a matching `### Articolul N` heading in the
  latest `.md` file and must resolve through `LegalIndex.article(doc, N)`.
- Every anchor-based case must have the anchor `id` in the original HTML and a
  matching `expected_terms` list for the Markdown baseline.
- Verify the answer in the rendered HTML/Markdown before adding the case. Do
  not pick an article merely because the index ranks it.

## Benchmark library (`src/scrapper_legislatie_just/benchmark.py`)

Implement these pieces.

### Data types

- `BenchmarkCase`: question, doc, expected articles/anchors, expected terms.
- `MethodHit`: doc, article number, anchor/link, text, rank, source path.
- `CaseResult`: case, method, mode, hits, matched rank, latency, context chars.
- `BenchmarkReport`: corpus metadata, per-method aggregates, per-case rows,
  artifact paths.

### Shared tokenization and scoring

Import the existing helpers from `query.py` (`normalize`, `_query_tokens`,
`_stem`, `_proximity`, `STOPWORDS`) or promote them to public names without
changing behavior. Both methods must use the same normalization, stop-word
list, stemming, IDF and proximity formula so the comparison is about retrieval
structure, not tokenizer differences.

### Method A: `MarkdownBaseline`

- Discover docs from `3-expanded/*/toc-*.json`; read `nume`, `descriere` and
  the latest `consolidari[].html_name` to locate the matching `.md`.
- `select_doc(question, k=3)`: lexical score over TOC `nume + descriere`.
- `search(question, doc=None, limit=10)`:
  - If `doc` is supplied, use it; otherwise use the top-ranked TOC doc.
  - Split Markdown into article chunks using a regex such as
    `^#{2,4}\s*Articolul\s+(\d+(?:\^\d+)?)`.
  - Stop a chunk at the next heading of level 2 or higher.
  - For documents without article headings (CAEN annexes, accounting norms),
    split into table rows/paragraph blocks and label a leading code when present.
  - Score chunks with query-token IDF, exact-token bonus, article-heading bonus
    and proximity; deduplicate by article number; return top `limit`.
  - Cache parsed chunks per document. Cold mode uses a fresh cache; warm mode
    reuses it, so report both.
- `context(question, doc=None, limit=4)`: join the top chunks with a
  `SURSA: file://.../<file>.md#L<start>-L<end>` line, capped at ~8,000 chars
  per chunk.

### Method B: `IndexedMethod`

- Wrap `LegalIndex.search(question, doc=..., limit=10)` for ranking.
- Wrap `LegalIndex.context(question, limit=4, doc=...)` for context and
  timing. This requires the `doc` parameter added below.
- Report `link`/anchor for every hit, and count how many hits have a valid HTML
  anchor (`anchor_coverage`).

### Matching

- Article-based case: hit if the candidate article number is in `articles`.
- Anchor-based case: for Method B, hit if the link anchor is in `anchors`; for
  Method A, hit if all `expected_terms` occur in the chunk text.
- Metrics: `any_hit@1/5/10`, `coverage@5` (mean fraction of expected units in
  top 5), `MRR`, `doc_hit@1/@3` (blind mode only).
- Blind mode: Method A chooses one doc from TOC metadata and searches only that
  doc; Method B searches all latest docs. Report doc-selection accuracy
  separately and do not gate it.

### Timing and overhead

- Per case and method, record:
  - cold first query (selection/read/parse/scoring or first DB query);
  - warm median and p95 over 3 repetitions;
  - `search` and `context` latency separately;
  - context size in characters.
- Record corpus overhead:
  - `4-processed/manifest.json` `build_seconds` (currently 39.3);
  - total size of `3-expanded/*.md`, `law.db`, `graph.db`;
  - break-even query count = `build_seconds / (legacy_warm_median - indexed_warm_median)`
    when the denominator is positive, otherwise `n/a`.
- Note in the report that the cost of building `3-expanded` with Docling is not
  measured or included.

### Report

- Write `tests/artifacts/retrieval_benchmark.json` with corpus metadata,
  aggregate metrics, per-case rows, timings and overhead.
- Write `tests/artifacts/retrieval_benchmark.md` with:
  - a one-line verdict;
  - an accuracy table per method and mode;
  - a latency table (cold, warm median, p95);
  - a per-case table with expected units, hit rank and pass/fail;
  - build/storage overhead and break-even;
  - a note that Method A returns Markdown line links, Method B returns exact
    HTML anchors.
- Print the same summary to stdout from the test.

## Source changes for fair known-doc context

In `LegalIndex.context`, add `doc: str | None = None` and pass it through to
`self.search(question, doc=doc, ...)`. Keep the default behavior unchanged.
Expose the same optional `doc` in the MCP `context` tool schema and in the CLI
`context` command (`--doc`). Add a small assertion to the existing needle tests
or a focused unit test that `context(..., doc=...)` only returns the requested
act.

## Tests (`tests/test_retrieval_benchmark.py`)

Module-level skip when `4-processed/law.db` or `3-expanded/` is missing, matching
`tests/test_real_corpus.py`.

1. `test_markdown_baseline_unit` — build temporary TOC/`.md` files; verify
   article chunking, doc selection, exact/stem ranking and context rendering.
   This test must not need the real corpus.
2. `test_gold_questions_are_resolvable` — for every case in
   `real_questions.jsonl`:
   - the `doc` exists in `3-expanded` and `4-processed`;
   - each `articles` value has a matching Markdown heading and resolves through
     `LegalIndex.article`;
   - each `anchors` value appears in the source HTML and has `expected_terms`.
3. `test_retrieval_benchmark` — run both methods in both modes, write the
   artifacts, print the summary, and assert:
   - every known-doc case returns non-empty evidence from both methods;
   - indexed `any_hit@5 >= legacy any_hit@5`;
   - indexed `any_hit@5 >= 0.80`.
   Do not assert latency. If the indexed method is slower, print a warning.

Keep the existing `tests/test_real_corpus.py` working; it will now run over the
expanded JSONL and ignore `expected_terms`/`notes`.

## Validation

```bash
uv run pytest tests/test_retrieval_benchmark.py -q
uv run pytest -q
uv run scrapper-legislatie-just-cli context "scutire impozit venit" --doc codul-fiscal --limit 4
```

Expected: the benchmark skips when the corpus is absent; with the local corpus
it produces both artifacts and passes the accuracy gate. All existing tests
still pass.

## Out of scope

- LLM-generated answers or an LLM judge.
- Timing the `expand`/Docling step or the `process` build.
- Network access or rebuilding the index during the test.
- Changing retrieval ranking in `query.py` beyond the optional `doc` filter.
