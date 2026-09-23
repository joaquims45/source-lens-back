# source-lens-api

Evidence-first repository intelligence backend for **SourceLens** — clone a
public GitHub repository, understand its code, search it, ask questions
about it, and see its architecture and call graph, all grounded in citable
source rather than an LLM's unverified word.

```
GitHub URL -> clone -> file discovery -> language detection -> Tree-sitter parsing
   -> symbol extraction -> code-aware chunking -> embeddings -> hybrid search index
   -> architecture graph -> call graph -> grounded chat agent
```

See [`docs/technical-design.md`](docs/technical-design.md) for the full
design (domain model, DB schema, retrieval/agent/architecture design,
security model, trade-offs) as approved before implementation began.

## Features

### Repository ingestion

Given a public GitHub URL, the backend clones it, discovers and sanitizes
its files, detects languages, parses supported languages (Python,
JavaScript, TypeScript, TSX) with Tree-sitter, extracts symbols and imports,
and splits files into syntax-aware chunks — never fixed-size text blocks.
Every stage reports real progress over a durable job (Postgres-backed,
Celery-executed, redeliverable if a worker dies mid-run) and is inspectable
through the REST API without any LLM involved. Files in unsupported
languages are still discovered and stored, marked `unsupported` rather than
silently skipped or misrepresented as parsed.

### Hybrid search

Every chunk is embedded and indexed for two independent rankings, combined
with Reciprocal Rank Fusion and reranked:

```
                 +-- semantic (pgvector cosine distance) --+
query -----------|                                         |-- RRF -- rerank -- results
                 +-- lexical (Postgres tsvector/ts_rank_cd) +
```

Every retrieved chunk keeps its semantic/lexical/fused/rerank scores and its
file/line citation, so retrieval stays inspectable and evaluable — the
repository is never sent to an LLM wholesale.

The default `EmbeddingProvider` is a deterministic, offline hashing scheme
(feature hashing over identifier-like tokens), not a trained code-embedding
model — it needs no API key, so the whole pipeline runs locally end to end.
`VoyageEmbeddings` (Voyage's code embedding API) is implemented behind the
same interface; set `EMBEDDING_PROVIDER=voyage` and `VOYAGE_API_KEY` to use
it. Swapping providers to a different vector dimension needs a new
migration, since `pgvector` columns are fixed-dimension. The reranker is
similarly a token-overlap baseline behind a `Reranker` protocol a
cross-encoder or hosted rerank API could swap into later.

`python -m sourcelens.evaluation.run` measures Recall@K, MRR and latency for
an already-ingested analysis against a `{question, expected_files}` dataset,
across all four retrieval strategies side by side:

```bash
uv run python -m sourcelens.evaluation.run \
  --analysis-id <uuid> --dataset eval_dataset.json --k 5
```

```json
[{"question": "How are orders created?", "expected_files": ["src/orders/order.service.ts"]}]
```

### Grounded chat agent

A single LangGraph agent answers questions about the repository using
bounded read tools: `search_code`, `find_symbol`, `read_file`,
`find_references`, `trace_dependency`, `get_file_tree` and
`get_repository_info`.

```
agent (LLM + tools) --tool call?--> tools --> harvest evidence --back to agent
        |
        no more tool calls
        v
      answer
```

Grounding is structural, not trust-based: **citations returned to the
caller are exactly the deduplicated tool evidence the graph harvested —
never text the model wrote**. A tool's result (`artifact`) is the only
source of citations, so an answer can't cite a file or line the agent
didn't actually retrieve. Tool output is fenced
(`<repository_content untrusted="true">`) and the system prompt states
explicitly that repository content is data, never instructions — a comment
reading "ignore previous instructions" is just text to quote, not something
the model obeys. A hard iteration cap forces a tools-off final turn instead
of ever looping forever.

Conversations and their citations persist to Postgres
(`conversations`/`chat_messages`), so a `conversation_id` continues a
thread. Responses are available both as a single JSON answer and as an SSE
stream of validated, structured blocks (`tool_call`, `evidence`, `answer`,
`done`) rather than raw token fragments.

The default chat model is Anthropic's API via `langchain-anthropic`
(`ANTHROPIC_API_KEY` required); without a key, `/chat` and `/chat/stream`
fail clearly (`agent_unconfigured`) while every other endpoint keeps
working. The whole graph — tool loop, evidence dedup, iteration cap — is
independently tested with a scripted fake chat model, the same
offline-first pattern used for embeddings.

### Architecture intelligence

The architecture graph is never built by the LLM: it's computed on demand
from facts already persisted during ingestion (files, symbols, imports)
plus the repository's own manifests. Every node/edge carries at least one
`Evidence(file, line, reason, origin)` and a confidence score — the domain
model enforces this (see `sourcelens.architecture.graph.GraphBuilder`), so
nothing in the graph is asserted without a citation.

```
module nodes ---- imports (heuristic token match) ---- module nodes
   |
component nodes (controller/service/repository/worker/authentication)
   |          classified from decorators (@app.get, @celery_app.task — high
   |          confidence) or name suffixes (*Service, *Repository — lower)
   v
infrastructure nodes (database/cache/queue/external_api/infrastructure)
   detected from Dockerfiles, docker-compose service images (strongest
   evidence) and dependency manifests, plus usage patterns inside a
   component's own source (redis.Redis(), session.query(...), httpx.get(...))
```

Computed-on-demand rather than stored: detector changes apply retroactively
to any past analysis without re-ingesting it. Test code (`tests/`,
`test_*.py`, `*.spec.ts`, …) is excluded before classification, so a test
function that happens to mention "jwt" in its name doesn't get
misclassified as an authentication component — a real misclassification a
smoke test against a public FastAPI repository caught before it shipped.

This is a heuristic baseline, not a resolved import graph: an import edge
cites the actual import statement but only *approximately* matches it to a
module by name. Treat low-confidence edges as leads, not certainties,
exactly as their `confidence` field signals.

### Dependency tracing

`sourcelens.tracing` answers "who calls this" / "what does this call" for a
selected function or method — a static, name-based call graph, computed on
demand like the architecture graph, and equally explicit about not being
full semantic resolution:

- `self.foo()` / `this.foo()` / `cls.foo()` resolves against sibling
  methods of the *same class* first (confidence 0.8) — the qualifier
  narrows the search.
- A bare `foo()` resolves against a repository-wide name index: exactly one
  symbol named `foo` → **resolved** (0.6); more than one (two unrelated
  classes each with a `save` method, say) → **ambiguous** (0.3, every
  candidate listed — never guessed); zero matches (a builtin or
  third-party call) → no edge at all, since there's nothing in the
  repository to cite.
- A definition's own signature (`def foo(`, `class Foo(`) is excluded from
  being read as a call to itself.

`GET /analyses/{id}/symbols/{symbol_id}/trace` returns the selected symbol
plus its callers/callees, each with the citing call site and its
resolved/ambiguous status.

### Production hardening

- **Security**: CORS is an explicit origin allow-list (no wildcard).
  `API_KEY`, unset by default for local single-operator use, gates every
  `/api/*` route behind an `X-API-Key` header when set.
- **Caching**: the architecture graph and call graph are pure, deterministic
  computations over an analysis's persisted facts — expensive to recompute
  (a full regex scan of every symbol's source) for no reason once an
  analysis is `completed` and therefore immutable. Both are cached in Redis
  with a TTL as a memory safety valve, not for invalidation — there's
  nothing to invalidate. A still-running analysis is always computed fresh,
  since caching a graph built from partial data would be wrong.
- **Observability**: `sourcelens.observability` logs retrieval (strategy,
  candidate count, top scores per component, latency) and agent turns
  (model, tools called, iterations, input/output tokens, latency, an
  estimated cost when the model is a recognized one) as structured fields.
  Every field is also a natural OpenTelemetry span attribute, so real
  tracing later means wrapping these call sites in spans, not redesigning
  what gets recorded.

## Architecture

- **API** (`sourcelens.main`): FastAPI app exposing submission and
  inspection endpoints.
- **Dispatcher** (`sourcelens.jobs.dispatcher`): polls an outbox table and
  hands queued/expired jobs to Celery — this is what makes redelivery after
  a worker crash automatic (a job's lease expiring makes it eligible
  again).
- **Worker** (Celery, `sourcelens.jobs.celery_app` / `sourcelens.jobs.tasks`):
  runs the ingestion pipeline for one job and reports real per-stage
  progress.
- **PostgreSQL** (with `pgvector`): durable state — repositories, analyses,
  jobs, stages, events, files, symbols, imports, chunks, conversations.
  Every analysis pins an immutable commit SHA and parser/chunker version.
- **Redis**: Celery's broker, and the architecture/call-graph cache.

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

Configuration is read from environment variables / `.env` — see
`.env.example` for every setting (embedding provider, agent model, clone/job
limits, CORS origins, API key, cache TTL) and `sourcelens.config.Settings`
for their defaults.

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
| `GET /analyses/{id}/search?q=&k=&strategy=` | Hybrid code search. `strategy` is `semantic`, `lexical`, `hybrid` or `hybrid_rerank` (default). Returns chunks with file/line citations and per-component scores. |
| `POST /analyses/{id}/chat` | Ask a grounded question. Body `{"question": "...", "conversation_id": null}`. Returns the answer plus citations; omit `conversation_id` to start a new thread, pass it back to continue one. |
| `GET /analyses/{id}/chat/stream?q=&conversation_id=` | Same as above, streamed as SSE events: `tool_call`, `evidence`, `answer`, then `done` (with the conversation id). |
| `GET /analyses/{id}/architecture` | The full architecture graph: nodes and edges, each with its evidence and confidence. |
| `GET /analyses/{id}/architecture/components/{component_id}` | One component's evidence plus its incoming/outgoing dependency edges. |
| `GET /analyses/{id}/symbols/{symbol_id}/trace` | Who calls this symbol and what it calls, each with the citing call site and resolved/ambiguous status. |

Errors are returned as `application/problem+json` with a `request_id` that
matches the `X-Request-ID` response header and the structured logs.

## Security

Repository content is untrusted input: only public `https://github.com/...`
URLs are accepted, clones are shallow and time/size bounded, secrets and
`.env`-like files are excluded or redacted, symlinks are never followed, and
paths are validated against traversal. Repository code is never executed.

The agent extends this to the LLM boundary: everything a tool returns is
repository *data*, never instructions, both structurally (the system
prompt) and textually (an untrusted-content fence around tool output).

At the network boundary: CORS is an explicit origin allow-list, and
`API_KEY` gates every `/api/*` route via `X-API-Key` when set — unset by
default, since this is still meant as a local, single-operator deployment
rather than a publicly exposed one.

## Testing

```bash
uv run ruff check .
uv run mypy src
uv run pytest                              # unit tests only
RUN_INTEGRATION=1 uv run pytest            # + integration tests (needs postgres/redis up)
```

127 tests. Integration tests cover idempotent submission, lease-based job
redelivery, snapshot isolation between analyses, the ingestion task end to
end (against a fake clone step, so they don't depend on network access),
lexical/semantic/hybrid retrieval and evaluation against real
Postgres/pgvector, the agent's tool loop, grounding and conversation
persistence against a scripted chat model (no `ANTHROPIC_API_KEY` needed to
run the suite), architecture/call-graph detection against real committed
code, Redis caching (asserting the detection function only runs once across
repeated requests, not just that a cache function was called), and
structured logging (via structlog's `capture_logs`, asserting real emitted
fields).

The architecture and call-graph detectors, the CORS/API-key middleware and
the full ingestion pipeline were also verified end to end against a live
Docker stack and a real public repository
(`nsidnev/fastapi-realworld-example-app`), which is how the test-code
misclassification above, a Redis workspace permission bug, and a Celery
task-registration issue were actually caught before they shipped.
