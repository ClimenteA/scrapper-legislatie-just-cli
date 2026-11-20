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
# the single package, its scripts and the frozen lockfile. OCR (docling,
# easyocr) is a required dependency, so there is one image for both the web
# app and the indexer. torch/torchvision come from the CPU-only PyTorch index
# configured in pyproject.toml, so no nvidia-*/CUDA wheels are downloaded.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN pip install uv && uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["scrapper-legislatie-just-web"]
