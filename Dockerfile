FROM python:3.12-slim

# System packages:
#   tesseract-ocr — pytesseract shells out to this binary for OCR.
#   libnss-mdns   — resolves crosspoint.local (.local / mDNS). The NSS module
#                   talks to the host's avahi-daemon over the socket bind-
#                   mounted in docker-compose.yml (the container has no avahi of
#                   its own). Without libnss-mdns, .local names never resolve in
#                   the image (nsswitch ships only "files dns"), which is why the
#                   watcher never detected the X4 in production while the test
#                   scripts (which resolve on the host and inject --add-host)
#                   worked.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libnss-mdns \
    && rm -rf /var/lib/apt/lists/* \
    && sed -i 's/^hosts:.*/hosts: files mdns4_minimal [NOTFOUND=return] dns/' /etc/nsswitch.conf

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency files first so this layer caches independently of
# source changes.
COPY pyproject.toml ./
RUN uv sync

COPY . .

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health', timeout=3)" || exit 1

# Runs the device watcher loop + KOReader sync server concurrently.
CMD ["python", "-m", "xteink_service"]
