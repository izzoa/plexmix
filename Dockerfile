FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsqlite3-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry

WORKDIR /app

COPY pyproject.toml poetry.lock ./

RUN poetry config virtualenvs.create false \
    && poetry install -E ui --only main --no-interaction --no-ansi

COPY . .

RUN poetry install -E ui --only main --no-interaction --no-ansi

ENV PLEXMIX_DATA_DIR=/data

RUN mkdir -p /data

VOLUME /data

EXPOSE 3000 8000

ENTRYPOINT ["poetry", "run", "plexmix"]
CMD ["ui", "--host", "0.0.0.0"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1
