# ---- Builder stage: install dependencies ----
FROM python:3.12-slim AS builder

ARG WITH_LOCAL=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsqlite3-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry

WORKDIR /app

COPY pyproject.toml poetry.lock ./

# Install runtime deps into an in-project venv
# WITH_LOCAL=true adds sentence-transformers + PyTorch for offline embeddings/LLM
RUN poetry config virtualenvs.in-project true \
    && if [ "$WITH_LOCAL" = "true" ]; then \
         poetry install -E ui -E local --without dev --no-root --no-interaction --no-ansi; \
       else \
         poetry install -E ui --without dev --no-root --no-interaction --no-ansi; \
       fi

# Install essentia (audio analysis) — prebuilt wheels exist for amd64 only
# On arm64 this is a no-op; audio analysis will show as unavailable in the UI
RUN .venv/bin/pip install --no-cache-dir essentia 2>/dev/null || echo "essentia not available for this platform — audio analysis disabled"

COPY . .

RUN if [ "$WITH_LOCAL" = "true" ]; then \
      poetry install -E ui -E local --without dev --no-interaction --no-ansi; \
    else \
      poetry install -E ui --without dev --no-interaction --no-ansi; \
    fi

# ---- Runtime stage: slim final image ----
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsqlite3-0 \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry

WORKDIR /app

# Copy app code + in-project virtualenv from builder
COPY --from=builder /app /app

# Tell Poetry to use the in-project venv
RUN poetry config virtualenvs.in-project true

ENV PLEXMIX_DATA_DIR=/data
ENV PATH="/app/.venv/bin:$PATH"

# Configurable ports (override with -e at runtime)
ENV PLEXMIX_UI_PORT=3000
ENV PLEXMIX_BACKEND_PORT=8000
# Set PLEXMIX_API_URL when external ports differ from internal (e.g. Docker port mapping)
# Example: PLEXMIX_API_URL=http://myhost:8154
# Set PLEXMIX_ALLOWED_HOSTS for custom domain access (e.g. reverse proxy)
# Example: PLEXMIX_ALLOWED_HOSTS=plexmix.example.com

RUN mkdir -p /data

VOLUME /data

EXPOSE ${PLEXMIX_UI_PORT} ${PLEXMIX_BACKEND_PORT}

ENTRYPOINT ["poetry", "run", "plexmix"]
CMD ["ui", "--host", "0.0.0.0"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:${PLEXMIX_BACKEND_PORT:-8000}/ping || exit 1
