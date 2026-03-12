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

RUN mkdir -p /data

VOLUME /data

EXPOSE 3000 8000

ENTRYPOINT ["poetry", "run", "plexmix"]
CMD ["ui", "--host", "0.0.0.0"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1
