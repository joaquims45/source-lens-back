FROM python:3.13.7-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.12.17
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY migrations ./migrations
COPY alembic.ini ./
RUN useradd --create-home --uid 10001 sourcelens && mkdir -p /work && chown sourcelens /work
USER sourcelens
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
CMD ["uvicorn", "sourcelens.main:app", "--host", "0.0.0.0", "--port", "8000"]
