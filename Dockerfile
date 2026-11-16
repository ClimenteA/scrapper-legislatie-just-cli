FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Bucharest

# tzdata is required by zoneinfo (token expiry / scheduler timezone).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The download step needs monolith to save printable pages from legislatie.just.ro.
RUN curl -fLsS \
    https://github.com/Y2Z/monolith/releases/latest/download/monolith-cli-linux-x86_64 \
    -o /usr/local/bin/monolith \
    && chmod +x /usr/local/bin/monolith

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# The web app never reads PDFs, so it uses the lean base install. The indexer
# needs the OCR stack for base64 images embedded in downloaded HTML.
ARG INSTALL_OCR=0
RUN pip install uv \
    && if [ "$INSTALL_OCR" = "1" ]; then \
         uv sync --frozen --no-dev --extra ocr; \
       else \
         uv sync --frozen --no-dev; \
       fi
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["scrapper-legislatie-just-web"]
