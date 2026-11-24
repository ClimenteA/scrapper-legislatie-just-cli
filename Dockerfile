FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Bucharest

# tzdata is required by zoneinfo (token expiry / scheduler timezone).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The indexer needs monolith to save printable pages from legislatie.just.ro.
RUN curl -fLsS \
    https://github.com/Y2Z/monolith/releases/latest/download/monolith-cli-linux-x86_64 \
    -o /usr/local/bin/monolith \
    && chmod +x /usr/local/bin/monolith

WORKDIR /app
# Build context is the repository root (see docker-compose.yml), which holds
# the single package, its scripts and the frozen lockfile. One image serves
# both the web app and the indexer: PDF -> Markdown uses LiteParse with the
# ocria-nano OCR model, HTML -> Markdown uses markdownify, and the index is a
# deterministic BM25 (llama-index) build. No torch, no Docling, no embeddings.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN pip install uv && uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"

# The OCR model is downloaded once into the bind-mounted data directory, so a
# recreated container reuses it instead of downloading ~60 MB again.
ENV LITEPARSE_MODEL_DIR=/data/legislatie-descarcata/models

EXPOSE 8000
CMD ["scrapper-legislatie-just-web"]
