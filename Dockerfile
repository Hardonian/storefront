# Build and run storefront as a hardened, non-root microservice
FROM python:3.12-slim-bookworm AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast, reliable dependency isolation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy dependencies manifest
COPY pyproject.toml .
RUN uv venv /opt/venv && . /opt/venv/bin/activate && uv pip install --no-cache -e .

# Final Stage: Lean production runner
FROM python:3.12-slim-bookworm AS runner

WORKDIR /app

# Create non-privileged service user
RUN groupadd -g 1001 storefront && \
    useradd -u 1001 -g storefront -s /bin/bash -m storefront

# Copy virtual environment
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Copy application source code
COPY --chown=storefront:storefront app ./app
COPY --chown=storefront:storefront static ./static
COPY --chown=storefront:storefront flags.json ./flags.json

# Create writable data directories
RUN mkdir -p /data /data/bundles /data/legal /data/drafts && \
    chown -R storefront:storefront /data

USER storefront

EXPOSE 8020

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8020/health').read()" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8020", "--no-access-log"]
