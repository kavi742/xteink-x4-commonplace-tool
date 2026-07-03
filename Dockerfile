FROM python:3.12-slim

# tesseract-ocr is a system binary, not a Python package — pytesseract
# (in pyproject.toml) only calls out to it, so it has to be installed here.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency files first so this layer caches independently of
# source changes.
COPY pyproject.toml ./
RUN uv sync

COPY . .

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8081/health', timeout=3)" || exit 1

# xteink_service/ doesn't exist yet (see pyproject.toml's [tool.uv]
# package = false note) — this build succeeds today, but the run step
# will fail until Phase 4+ adds the actual module.
CMD ["python", "-m", "xteink_service"]
