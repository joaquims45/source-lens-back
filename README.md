# source-lens-api

Evidence-first repository intelligence backend for **SourceLens**. This README
covers what exists today: **Milestone 1 — Repository Intelligence**. Search
(embeddings, hybrid retrieval), the LangGraph agent and architecture
intelligence are later milestones and are not implemented yet.

## What Milestone 1 does

Given a public GitHub repository URL, the backend clones it, discovers and
sanitizes its files, detects languages, parses supported languages with
Tree-sitter, extracts symbols/imports, and splits files into syntax-aware
chunks — all inspectable through a REST API without any LLM involved. See
[`docs/technical-design.md`](docs/technical-design.md) for the full design
and its trade-offs.

```
GitHub URL -> clone -> file discovery -> language detection ->
Tree-sitter parsing -> symbol extraction -> code-aware chunking -> persisted analysis
```

Supported languages: Python, JavaScript, TypeScript, TSX. Other files are
still discovered and stored (for the future file explorer) but marked
`unsupported` rather than silently skipped or misrepresented as parsed.

## Architecture

- **API** (`sourcelens.main`): FastAPI app exposing submission and inspection
  endpoints.
- **Dispatcher** (`sourcelens.jobs.dispatcher`): polls an outbox table and
  hands queued/expired jobs to Celery — this is what makes redelivery after a
  worker crash automatic (a job's lease expiring makes it eligible again).
- **Worker** (Celery, `sourcelens.jobs.celery_app` / `sourcelens.jobs.tasks`):
  runs the ingestion pipeline for one job and reports real per-stage progress.
- **PostgreSQL**: durable state — repositories, analyses, jobs, stages,
  events, files, symbols, imports, chunks. Every analysis pins an immutable
  commit SHA and parser/chunker version.
- **Redis**: Celery's broker.

## Running locally

```bash
docker compose up -d           # postgres, redis, api, worker, dispatcher
curl http://127.0.0.1:8000/health/live
```

The `api` container runs pending Alembic migrations on startup. Without
Docker, run each piece against the `postgres`/`redis` services from
`compose.yaml`:

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn sourcelens.main:app --reload
uv run python -m sourcelens.jobs.dispatcher
uv run celery -A sourcelens.jobs.celery_app worker --loglevel=INFO -Q ingestion
```

Configuration is read from environment variables / `.env` (see
`.env.example`); see `sourcelens.config.Settings` for every limit (clone
timeout, max repository size, max files, job lease duration, etc).

## API

All endpoints are under `/api/v1`.

| Method & path | Purpose |
| --- | --- |
| `POST /repositories` | Submit a repository for analysis. Requires an `Idempotency-Key` header; body is `{"url": "...", "ref": "optional-branch-or-tag"}`. Returns `repository_id`, `analysis_id`, `job_id`. |
| `GET /analyses/{id}` | Analysis status, commit SHA, versions, capabilities, stats, and per-stage progress. |
| `GET /analyses/{id}/events` | Server-Sent Events stream of job progress (`stage.*`, `analysis.*`). |
| `GET /analyses/{id}/files` | File list with language and parse status. |
| `GET /analyses/{id}/files/{file_id}` | File content plus its symbols and imports. |
| `GET /analyses/{id}/symbols?q=` | Symbols for the analysis, optionally filtered by qualified name. |

Errors are returned as `application/problem+json` with a `request_id` that
matches the `X-Request-ID` response header and the structured logs.

## Security

Repository content is untrusted input: only public `https://github.com/...`
URLs are accepted, clones are shallow and time/size bounded, secrets and
`.env`-like files are excluded or redacted, symlinks are never followed, and
paths are validated against traversal. Repository code is never executed.

## Testing

```bash
uv run ruff check .
uv run mypy src
uv run pytest                              # unit tests only
RUN_INTEGRATION=1 uv run pytest            # + integration tests (needs postgres/redis up)
```

Integration tests cover idempotent submission, lease-based job redelivery,
snapshot isolation between analyses, and the ingestion task end to end
(against a fake clone step, so they don't depend on network access).
